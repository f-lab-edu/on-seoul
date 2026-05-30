"""AnswerAgent 단위 테스트.

답변 생성, 시설 카드 정규화, 제목 생성, fallback URL 처리를 검증한다.
"""

import json
from unittest.mock import AsyncMock, MagicMock

from tests.helpers import make_agent_state
from agents.answer_agent import AnswerAgent, _AnswerOutput, _TitleOutput
from schemas.state import AgentState, IntentType


def _make_state(**kwargs) -> AgentState:
    return make_agent_state(intent=IntentType.SQL_SEARCH, **kwargs)


def _make_agent(
    answer_text: str = "수영장 목록입니다.",
    title_text: str | None = None,
) -> AnswerAgent:
    agent = AnswerAgent.__new__(AnswerAgent)

    mock_answer_chain = MagicMock()
    mock_answer_chain.ainvoke = AsyncMock(
        return_value=_AnswerOutput(answer=answer_text)
    )
    agent._answer_chain = mock_answer_chain

    mock_title_chain = MagicMock()
    mock_title_chain.ainvoke = AsyncMock(
        return_value=_TitleOutput(title=title_text or "수영장 조회")
    )
    agent._title_chain = mock_title_chain

    return agent


class TestAnswerAgent:
    async def test_answer_populates_answer_field(self):
        """answer 메서드는 생성된 답변을 state.answer에 채운다."""
        agent = _make_agent("강남구 수영장은 현재 접수 중입니다.")
        result = await agent.answer(_make_state())

        assert result["answer"] == "강남구 수영장은 현재 접수 중입니다."

    async def test_title_not_generated_when_not_needed(self):
        """title_needed=False면 title_chain이 호출되지 않고 title은 None이다."""
        agent = _make_agent()
        result = await agent.answer(_make_state(title_needed=False))

        agent._title_chain.ainvoke.assert_not_called()
        assert result.get("title") is None

    async def test_title_generated_when_needed(self):
        """title_needed=True면 title_chain이 호출되고 title이 채워진다."""
        agent = _make_agent(title_text="수영장 안내")
        result = await agent.answer(_make_state(title_needed=True))

        agent._title_chain.ainvoke.assert_called_once()
        assert result["title"] == "수영장 안내"

    async def test_answer_chain_receives_message_and_results(self):
        """answer_chain에 message, results_json, extra_count가 전달된다."""
        agent = _make_agent()
        rows = [{"service_name": "수영장", "service_url": "https://example.com"}]
        state = _make_state(message="수영장", sql_results=rows)

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        assert call_kwargs["message"] == "수영장"
        assert "수영장" in call_kwargs["results_json"]
        assert call_kwargs["extra_count"] == 0

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
        from agents.answer_agent import _FALLBACK_URL, AnswerAgent

        row = {"service_id": "S001", "service_name": "수영장", "service_url": None}
        normalized = AnswerAgent._normalize(row)

        assert normalized["service_url"] == _FALLBACK_URL

    async def test_normalize_keeps_existing_url(self):
        """service_url이 있으면 그대로 유지된다."""
        from agents.answer_agent import AnswerAgent

        row = {
            "service_id": "S001",
            "service_name": "수영장",
            "service_url": "https://yeyak.seoul.go.kr/svc/001",
        }
        normalized = AnswerAgent._normalize(row)

        assert normalized["service_url"] == "https://yeyak.seoul.go.kr/svc/001"

    def test_normalize_converts_datetime_to_isoformat(self):
        """receipt_*_dt datetime 객체는 ISO 8601('T' 구분자) 문자열로 변환된다.

        프론트 계약(chat-service-cards-interface §5) 정합성 — sse_frame 의
        default=str 폴백(공백 구분자)에 의존하지 않고 _normalize 단에서 보장한다.
        """
        from datetime import date, datetime

        from agents.answer_agent import AnswerAgent

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
        from agents.answer_agent import AnswerAgent

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
        from agents.answer_agent import _FALLBACK_URL, AnswerAgent

        normalized = AnswerAgent._normalize(
            {"service_id": "S001", "service_url": "javascript:alert(1)"}
        )

        assert normalized["service_url"] == _FALLBACK_URL

    def test_normalize_rejects_non_http_scheme(self):
        """http(s) 외 스킴(ftp 등) service_url 은 fallback URL 로 강등된다."""
        from agents.answer_agent import _FALLBACK_URL, AnswerAgent

        normalized = AnswerAgent._normalize(
            {"service_id": "S001", "service_url": "ftp://files.example.com/a"}
        )

        assert normalized["service_url"] == _FALLBACK_URL

    def test_normalize_keeps_valid_https_url(self):
        """정상 https service_url 은 그대로 유지된다."""
        from agents.answer_agent import AnswerAgent

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
        from agents.answer_agent import AnswerAgent

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

    def test_missing_service_url_uses_fallback(self):
        """service_url이 없으면 yeyak fallback 링크가 사용된다."""
        from agents.answer_agent import _FALLBACK_URL, AnswerAgent

        normalized = AnswerAgent._normalize({"service_id": "S002"})
        assert normalized["service_url"] == _FALLBACK_URL

    def test_normalize_preserves_extended_fields_for_prompt(self):
        """LLM 프롬프트가 사용하는 확장 필드(분류·요금·대상·접수일정)가 모두 보존된다.

        service_open_*_dt(이용 기간) 는 LLM 컨텍스트에서 의도적으로 제외 —
        DB 에 비현실적 값(예: 2021~2031)이 많아 사용자 혼란을 유발하므로 답변에 노출하지 않는다.
        """
        from agents.answer_agent import AnswerAgent

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
        from agents.answer_agent import AnswerAgent

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
                {"service_id": "S1", "service_name": "테스트{시설}", "metadata": {"key": "val"}}
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

    async def test_five_or_fewer_results_no_extra(self):
        """결과 4건(DISPLAY_LIMIT 미만)이면 슬라이스 손실 없이 extra_count=0."""
        agent = _make_agent()
        state = _make_state(sql_results=self._make_rows(4))

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        displayed = json.loads(call_kwargs["results_json"])
        assert isinstance(displayed, list)
        assert len(displayed) == 4
        assert call_kwargs["extra_count"] == 0

    async def test_exactly_display_limit_no_extra(self):
        """결과가 정확히 DISPLAY_LIMIT(5)건이면 슬라이스 손실 없이 extra_count=0."""
        agent = _make_agent()
        state = _make_state(sql_results=self._make_rows(5))

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        displayed = json.loads(call_kwargs["results_json"])
        assert len(displayed) == 5
        assert call_kwargs["extra_count"] == 0

    async def test_six_results_sliced_to_five_with_extra_one(self):
        """결과 6건이면 상위 5건만 results_json에, extra_count=1이 전달된다."""
        agent = _make_agent()
        state = _make_state(sql_results=self._make_rows(6))

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        displayed = json.loads(call_kwargs["results_json"])
        assert len(displayed) == 5
        assert displayed[0]["service_id"] == "S001"  # RRF 순위 첫 번째 보존
        assert call_kwargs["extra_count"] == 1

    async def test_ten_results_sliced_to_five_with_extra_five(self):
        """결과 10건이면 extra_count=5."""
        agent = _make_agent()
        state = _make_state(sql_results=self._make_rows(10))

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        assert len(json.loads(call_kwargs["results_json"])) == 5
        assert call_kwargs["extra_count"] == 5

    async def test_empty_results_extra_count_zero(self):
        """결과 0건이면 extra_count=0."""
        agent = _make_agent()
        state = _make_state(sql_results=[])

        await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        assert call_kwargs["extra_count"] == 0

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

    async def test_service_cards_respects_display_limit(self):
        """10건 입력 → service_cards 5건 (extra_count=5 와 일관)."""
        agent = _make_agent()
        state = _make_state(sql_results=self._make_rows(10))

        result = await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        assert len(result["service_cards"]) == 5
        assert call_kwargs["extra_count"] == 5

    async def test_service_cards_at_display_limit_boundary(self):
        """경계 회귀: 입력이 정확히 _DISPLAY_LIMIT(5) 건 → service_cards 5건, extra_count=0.

        off-by-one 회귀를 방지한다 (display 슬라이스 [:_DISPLAY_LIMIT]).
        """
        agent = _make_agent()
        state = _make_state(sql_results=self._make_rows(5))

        result = await agent.answer(state)

        call_kwargs = agent._answer_chain.ainvoke.call_args[0][0]
        assert len(result["service_cards"]) == 5
        assert call_kwargs["extra_count"] == 0

    async def test_service_cards_empty_when_no_results(self):
        """검색 결과 0건 → service_cards == [] (None 이 아닌 빈 배열)."""
        agent = _make_agent("죄송합니다, 조건에 맞는 시설을 찾지 못했습니다.")
        state = _make_state(sql_results=None, vector_results=None, map_results=None)

        result = await agent.answer(state)

        assert result["service_cards"] == []

    async def test_service_cards_are_shallow_copies_not_aliases(self):
        """회귀: service_cards 의 dict 가 원본 검색 결과(sql_results) 와 다른 객체여야 한다.

        구현은 `[dict(card) for card in display]` 로 top-level dict 를 복제한다.
        cache envelope / SSE final payload 가 원본 state 결과와 같은 참조를 들고
        있으면, 향후 LLM 전처리 단계가 display 를 inplace mutate 할 때 외부 노출
        경로가 오염된다. 각 카드가 별개 객체임을 명시적으로 보장한다.
        """
        agent = _make_agent()
        rows = self._make_rows(3)
        state = _make_state(sql_results=rows)

        result = await agent.answer(state)

        cards = result["service_cards"]
        # 컨테이너 리스트도, 각 dict 도 원본과 다른 객체여야 한다.
        assert cards is not rows
        for card, row in zip(cards, rows):
            assert card is not row

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

    async def test_mutating_source_result_does_not_pollute_service_card(self):
        """회귀: 원본 sql_results top-level 키를 mutate 해도 service_cards 가 오염되지 않는다.

        역방향 분리 검증 — display 원소가 이후 inplace mutate 되어도 이미 노출된
        service_cards 스냅샷은 안전해야 한다.
        """
        agent = _make_agent()
        rows = self._make_rows(2)
        state = _make_state(sql_results=rows)

        result = await agent.answer(state)
        snapshot_name = result["service_cards"][0]["service_name"]

        rows[0]["service_name"] = "원본_변경"
        assert result["service_cards"][0]["service_name"] == snapshot_name

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
        assert call_kwargs["extra_count"] == 0
