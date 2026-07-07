"""AnswerAgent 단위 테스트 — core(answer/정규화/카드 슬라이스).

답변 생성, 시설 카드 정규화, fallback URL 처리, display 슬라이스를 검증한다.
(test_answer_agent.py 분할: core)
"""

import json

from agents.answer_agent import (
    AnswerAgent,
    _DISPLAY_LIMIT,
    _more_notice,
    _FALLBACK_URL,
    _CLAUSE_RESERVATION_GUIDE,
)
from tests._answer_support import _make_state, _make_agent


class TestAnswerAgent:
    async def test_answer_populates_answer_field(self):
        """answer 메서드는 생성된 답변을 state.answer에 채운다."""
        agent = _make_agent("강남구 수영장은 현재 접수 중입니다.")
        result = await agent.answer(_make_state())

        assert result["answer"] == "강남구 수영장은 현재 접수 중입니다."

    async def test_answer_does_not_set_title(self):
        """제목 생성은 generate_title_node 로 분리됐다 — answer 는 title 을 채우지 않는다."""
        agent = _make_agent()
        result = await agent.answer(_make_state(title_needed=True))

        assert "title" not in result

    async def test_answer_chain_receives_message_and_results(self):
        """answer_chain에 message, results_json, more_notice가 전달된다."""
        agent = _make_agent()
        rows = [{"service_name": "수영장", "service_url": "https://example.com"}]
        state = _make_state(message="수영장", sql_results=rows)

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        assert call_kwargs["message"] == "수영장"
        assert "수영장" in call_kwargs["results_json"]
        # extra_count=0 → more_notice는 금지 지시 문구이며 렌더 가능한 "0"이 없다.
        assert call_kwargs["more_notice"] == _more_notice(0)

    async def test_collect_results_merges_sql_and_vector(self):
        """sql_results와 vector_results가 모두 있으면 합쳐서 전달된다."""
        agent = _make_agent()
        sql_rows = [
            {"service_id": "S001", "service_name": "수영장", "service_url": None}
        ]
        vec_rows = [
            {"service_id": "S002", "service_name": "체험관", "service_url": None}
        ]
        state = _make_state(sql_results=sql_rows, vector_results=vec_rows)

        await agent.answer(state)

        results_json = agent._answer_chain.ainvoke.call_args[0][0]["results_json"]
        assert "수영장" in results_json
        assert "체험관" in results_json

    async def test_normalize_uses_fallback_url_when_missing(self):
        """service_url이 없으면 fallback URL로 대체된다."""
        row = {"service_id": "S001", "service_name": "수영장", "service_url": None}
        normalized = AnswerAgent._normalize(row)

        assert normalized["service_url"] == _FALLBACK_URL

    async def test_normalize_keeps_existing_url(self):
        """service_url이 있으면 그대로 유지된다."""
        row = {
            "service_id": "S001",
            "service_name": "수영장",
            "service_url": "https://yeyak.seoul.go.kr/svc/001",
        }
        normalized = AnswerAgent._normalize(row)

        assert normalized["service_url"] == "https://yeyak.seoul.go.kr/svc/001"

    def test_normalize_converts_datetime_to_isoformat(self):
        """receipt_*_dt datetime 객체는 ISO 8601('T' 구분자) 문자열로 변환된다.

        프론트 계약 정합성 — sse_frame 의
        default=str 폴백(공백 구분자)에 의존하지 않고 _normalize 단에서 보장한다.
        """
        from datetime import date, datetime

        normalized = AnswerAgent._normalize(
            {
                "service_id": "S001",
                "service_url": "https://example.com",
                "receipt_start_dt": datetime(2025, 11, 1, 9, 0, 0),
                "receipt_end_dt": date(2025, 12, 31),
            }
        )

        assert normalized["receipt_start_dt"] == "2025-11-01T09:00:00"
        assert "T" in normalized["receipt_start_dt"]
        # date 객체도 isoformat (시간부 없음)
        assert normalized["receipt_end_dt"] == "2025-12-31"

    def test_normalize_keeps_str_dt_and_none(self):
        """receipt_*_dt 가 이미 str 이면 그대로, None 이면 None 으로 통과한다."""
        normalized = AnswerAgent._normalize(
            {
                "service_id": "S001",
                "service_url": "https://example.com",
                "receipt_start_dt": "2025-11-01T00:00:00",
                "receipt_end_dt": None,
            }
        )

        assert normalized["receipt_start_dt"] == "2025-11-01T00:00:00"
        assert normalized["receipt_end_dt"] is None

    def test_normalize_rejects_javascript_scheme_url(self):
        """javascript: 스킴 service_url 은 fallback URL 로 강등된다 (XSS 방어)."""
        normalized = AnswerAgent._normalize(
            {"service_id": "S001", "service_url": "javascript:alert(1)"}
        )

        assert normalized["service_url"] == _FALLBACK_URL

    def test_normalize_rejects_non_http_scheme(self):
        """http(s) 외 스킴(ftp 등) service_url 은 fallback URL 로 강등된다."""
        normalized = AnswerAgent._normalize(
            {"service_id": "S001", "service_url": "ftp://files.example.com/a"}
        )

        assert normalized["service_url"] == _FALLBACK_URL

    def test_normalize_keeps_valid_https_url(self):
        """정상 https service_url 은 그대로 유지된다."""
        normalized = AnswerAgent._normalize(
            {"service_id": "S001", "service_url": "https://yeyak.seoul.go.kr/svc/1"}
        )

        assert normalized["service_url"] == "https://yeyak.seoul.go.kr/svc/1"

    async def test_answer_preserves_state_fields(self):
        """answer는 answer/title 외 나머지 state를 보존한다."""
        agent = _make_agent()
        state = _make_state(room_id=42, message_id=7)

        result = await agent.answer(state)

        assert result["room_id"] == 42
        assert result["message_id"] == 7

    async def test_collect_results_both_none_returns_empty_list(self):
        """sql_results와 vector_results가 모두 None이면 빈 결과로 답변을 생성한다."""
        agent = _make_agent("죄송합니다, 조건에 맞는 시설을 찾지 못했습니다.")
        state = _make_state(sql_results=None, vector_results=None, map_results=None)

        result = await agent.answer(state)

        # answer_chain은 여전히 호출되어야 한다
        agent._answer_chain.ainvoke.assert_called_once()
        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        # 빈 결과 목록 JSON이 전달되어야 한다
        assert json.loads(call_kwargs["results_json"]) == []
        assert result["answer"] == "죄송합니다, 조건에 맞는 시설을 찾지 못했습니다."

    async def test_collect_results_map_features_unpacked(self):
        """map_results의 features[].properties가 결과 목록에 포함된다."""
        agent = _make_agent()
        map_results = {
            "features": [
                {"properties": {"service_name": "체육관A", "area_name": "마포구"}},
                {"properties": {"service_name": "체육관B", "area_name": "서대문구"}},
            ]
        }
        state = _make_state(map_results=map_results)

        await agent.answer(state)

        results_json = agent._answer_chain.ainvoke.call_args[0][0]["results_json"]
        assert "체육관A" in results_json
        assert "체육관B" in results_json


class TestAnswerAgentVectorResultsFlatSchema:
    """vector_results가 sql_results와 동일한 평탄 스키마인 경우 _normalize 동작."""

    def test_flat_vector_row_normalized_without_metadata_unpack(self):
        """metadata 키가 없는 평탄 행에서도 모든 필드가 추출된다."""
        flat_row = {
            "service_id": "S001",
            "service_name": "마포 수영장",
            "area_name": "마포구",
            "place_name": "마포 스포츠센터",
            "service_status": "접수중",
            "receipt_start_dt": "2026-05-01",
            "receipt_end_dt": "2026-05-31",
            "service_url": "https://example.com/s001",
            "rrf_score": 0.123,
        }
        normalized = AnswerAgent._normalize(flat_row)
        assert normalized["service_id"] == "S001"
        assert normalized["service_name"] == "마포 수영장"
        assert normalized["area_name"] == "마포구"
        assert normalized["service_status"] == "접수중"
        assert normalized["service_url"] == "https://example.com/s001"

    # 누락 service_url → fallback 은 TestAnswerAgent.test_normalize_uses_fallback_url_when_missing
    # 과 동일 분기/단언이라 축소했다(평탄 스키마 추출·확장필드 보존은 아래 유지).

    def test_normalize_preserves_extended_fields_for_prompt(self):
        """LLM 프롬프트가 사용하는 확장 필드(분류·요금·대상·접수일정)가 모두 보존된다.

        service_open_*_dt(이용 기간) 는 LLM 컨텍스트에서 의도적으로 제외 —
        DB 에 비현실적 값(예: 2021~2031)이 많아 사용자 혼란을 유발하므로 답변에 노출하지 않는다.
        """
        row = {
            "service_id": "S100",
            "service_name": "마루공원 테니스장 1면",
            "area_name": "강남구",
            "place_name": "마루공원",
            "max_class_name": "체육시설",
            "min_class_name": "테니스장",
            "service_status": "접수중",
            "payment_type": "무료",
            "target_info": "제한없음",
            "receipt_start_dt": "2026-05-08",
            "receipt_end_dt": "2026-12-31",
            "service_open_start_dt": "2026-05-08",
            "service_open_end_dt": "2026-12-31",
            "service_url": "https://yeyak.seoul.go.kr/web/reservation/selectReservView.do?rsv_svc_id=S100",
        }
        n = AnswerAgent._normalize(row)
        assert n["max_class_name"] == "체육시설"
        assert n["min_class_name"] == "테니스장"
        assert n["payment_type"] == "무료"
        assert n["target_info"] == "제한없음"
        # 이용 기간 필드는 의도적으로 제외 (DB 신뢰성 이슈)
        assert "service_open_start_dt" not in n
        assert "service_open_end_dt" not in n
        # 시설별 service_url 보존 — fallback URL 로 덮이지 않아야 한다
        assert "rsv_svc_id=S100" in n["service_url"]


class TestAnswerAgentLangChainCompat:
    """LangChain ChatPromptTemplate 호환성 회귀 테스트.

    프롬프트의 `{...}` placeholder 잔재가 ValueError 를 일으키지 않는지,
    그리고 results_json 에 포함된 중괄호가 변수로 오인되지 않는지 검증.
    """

    def test_prompt_template_loads_without_value_error(self):
        """AnswerAgent() 초기화가 ChatPromptTemplate 파싱 오류 없이 성공한다."""
        # 실제 LLM 호출은 안 하지만 ChatPromptTemplate.from_messages 가 호출됨.
        # 만약 _ANSWER_SYSTEM 에 미escape `{var}` 잔재가 있으면 여기서 ValueError.
        try:
            AnswerAgent()
        except ValueError as e:
            if "Invalid variable name" in str(e):
                raise AssertionError(
                    f"_ANSWER_SYSTEM 프롬프트에 미escape placeholder 잔재: {e}"
                ) from e
            raise

    async def test_results_json_with_curly_braces_does_not_break_prompt(self):
        """results_json (JSON 직렬화 결과) 에 중괄호가 포함돼도 프롬프트가 깨지지 않는다.

        LangChain 의 ChatPromptTemplate 은 system/human 메시지에서만 변수를 치환하며,
        변수 값 자체에 포함된 `{` 는 추가 파싱 대상이 아니다. 회귀 방지 차원에서
        실제 JSON 입력으로 ainvoke 흐름을 한 번 더 검증한다.
        """
        agent = _make_agent("응답 내용")
        state = _make_state(
            sql_results=[
                {
                    "service_id": "S1",
                    "service_name": "테스트{시설}",
                    "metadata": {"key": "val"},
                }
            ]
        )
        # 예외 없이 통과하면 OK
        result = await agent.answer(state)
        assert result["answer"] == "응답 내용"


class TestAnswerAgentDisplaySlice:
    """상위 5건 슬라이스 + extra_count 코드 수준 강제 테스트."""

    def _make_rows(self, n: int) -> list[dict]:
        return [
            {"service_id": f"S{i:03d}", "service_name": f"시설{i}", "service_url": None}
            for i in range(1, n + 1)
        ]

    async def test_six_results_sliced_to_five_with_extra_one(self):
        """대표 케이스: 6건 → 상위 5건만 results_json, more_notice '외 1건', RRF 순위 보존.

        슬라이스+extra_count 로직의 대표 케이스. 4/5/10건 등 값만 다른 순열은
        동일 로직이라 축소했고, 5건 경계(off-by-one)는
        test_service_cards_at_display_limit_boundary 가 별도로 고정한다.
        """
        agent = _make_agent()
        state = _make_state(sql_results=self._make_rows(6))

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        displayed = json.loads(call_kwargs["results_json"])
        assert len(displayed) == _DISPLAY_LIMIT
        assert displayed[0]["service_id"] == "S001"  # RRF 순위 첫 번째 보존
        assert call_kwargs["more_notice"] == _more_notice(1)

    async def test_empty_results_extra_count_zero(self):
        """결과 0건이면 more_notice는 금지 지시 문구."""
        agent = _make_agent()
        state = _make_state(sql_results=[])

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        assert call_kwargs["more_notice"] == _more_notice(0)

    async def test_service_cards_populated_in_state(self):
        """answer() 호출 후 service_cards 슬롯에 LLM 컨텍스트와 동일한 dict 리스트가 담긴다."""
        agent = _make_agent()
        rows = self._make_rows(3)
        state = _make_state(sql_results=rows)

        result = await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        displayed = json.loads(call_kwargs["results_json"])

        assert isinstance(result["service_cards"], list)
        assert len(result["service_cards"]) == 3
        # LLM 컨텍스트로 전달된 display 와 동일한 dict 리스트여야 한다
        assert [c["service_id"] for c in result["service_cards"]] == [
            r["service_id"] for r in displayed
        ]

    async def test_service_cards_at_display_limit_boundary(self):
        """경계 회귀: 입력이 정확히 _DISPLAY_LIMIT(5) 건 → service_cards 5건, extra_count=0.

        off-by-one 회귀를 방지한다 (display 슬라이스 [:_DISPLAY_LIMIT]).
        """
        agent = _make_agent()
        state = _make_state(sql_results=self._make_rows(5))

        result = await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        assert len(result["service_cards"]) == _DISPLAY_LIMIT
        assert call_kwargs["more_notice"] == _more_notice(0)

    async def test_service_cards_empty_when_no_results(self):
        """검색 결과 0건 → service_cards == [] (None 이 아닌 빈 배열)."""
        agent = _make_agent("죄송합니다, 조건에 맞는 시설을 찾지 못했습니다.")
        state = _make_state(sql_results=None, vector_results=None, map_results=None)

        result = await agent.answer(state)

        assert result["service_cards"] == []

    async def test_mutating_service_card_does_not_pollute_source_results(self):
        """회귀: service_cards top-level 키를 mutate 해도 원본 sql_results 가 오염되지 않는다."""
        agent = _make_agent()
        rows = self._make_rows(2)
        original_first_name = rows[0]["service_name"]
        state = _make_state(sql_results=rows)

        result = await agent.answer(state)

        result["service_cards"][0]["service_name"] = "오염된_이름"
        # 원본 검색 결과는 그대로여야 한다 (shallow copy 분리).
        assert rows[0]["service_name"] == original_first_name

    # 역방향(원본 mutate → 카드 미오염) 분리는 위 forward 케이스와 동일한
    # dict(card) shallow-copy 불변식의 대칭 순열이라 축소했다.

    async def test_card_system_built_from_sliced_display_not_full_results(self):
        """회귀: 카드형 system 프롬프트는 슬라이스된 display(상위 5건) 기준으로 조립된다.

        6번째 이후에만 "접수중" 시설이 있고 상위 5건이 모두 비접수면,
        _build_card_system 은 display 만 보므로 _CLAUSE_RESERVATION_GUIDE 를
        포함하지 않는다. answer() 가 _build_card_system(message, display) 로
        호출하는 현재 동작(라인 323)을 고정한다 — all_results 로 바뀌면 RED.
        """
        agent = _make_agent()
        rows = [
            {
                "service_id": f"S{i:03d}",
                "service_name": f"시설{i}",
                "service_status": "예약마감",
            }
            for i in range(1, 6)
        ]
        # 6번째 행에만 접수중 — 슬라이스로 잘려나간다.
        rows.append(
            {"service_id": "S006", "service_name": "시설6", "service_status": "접수중"}
        )
        # 자치구 명시로 refine_hint 절은 배제하여 reservation_guide 판정만 격리.
        state = _make_state(sql_results=rows, message="강남구 시설")

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        system_prompt = call_kwargs["system"]
        assert _CLAUSE_RESERVATION_GUIDE not in system_prompt

    async def test_card_system_includes_guide_when_open_facility_within_top_five(self):
        """대조군: 접수중 시설이 상위 5건 안에 있으면 _CLAUSE_RESERVATION_GUIDE 포함."""
        agent = _make_agent()
        rows = [
            {"service_id": "S001", "service_name": "시설1", "service_status": "접수중"},
            {
                "service_id": "S002",
                "service_name": "시설2",
                "service_status": "예약마감",
            },
        ]
        state = _make_state(sql_results=rows, message="강남구 시설")

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        assert _CLAUSE_RESERVATION_GUIDE in call_kwargs["system"]

    async def test_hydrated_services_empty_plus_map_results(self):
        """hydrated_services=[]이고 map_results가 있으면 map features가 결과에 포함된다.

        MAP intent 에서 HydrationNode 가 hydrated_services=[] 를 설정하므로
        _collect_results 가 hydrated is not None 분기에서 [] 로 시작하되
        map_results 언팩이 정상적으로 이어져야 한다.
        """
        agent = _make_agent()
        map_results = {
            "features": [
                {"properties": {"service_id": "M001", "service_name": "근처체육관"}},
                {"properties": {"service_id": "M002", "service_name": "근처수영장"}},
            ]
        }
        state = _make_state(hydrated_services=[], map_results=map_results)

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        displayed = json.loads(call_kwargs["results_json"])
        service_names = [r["service_name"] for r in displayed]
        assert "근처체육관" in service_names
        assert "근처수영장" in service_names
        assert call_kwargs["more_notice"] == _more_notice(0)
