"""단일 엔티티 상세형 답변 경로 테스트 (VECTOR_SEARCH + identification).

지목 시설 1곳을 깊이 있게 안내하는 상세형 답변 경로(A+C+D)를 검증한다.
- A: identification 트리거 시에만 상세형 system 프롬프트로 분기 (회귀 금지).
- C: place_name 기준 그룹핑 — focal(RRF 최상위) 시설 우선 배치 + 보조 목록 분리.
- D: 구조화 필드(요금/접수기간/대상/공간목록) 본문 서술 지시.
"""

import json

from agents.answer_agent import (
    _DISPLAY_LIMIT,
    _STRUCT_CARD_LIST,
    _STRUCT_DETAIL,
    _focal_first,
    _group_by_place_name,
    _more_notice,
)
from schemas.state import IntentType
from tests.helpers import make_agent_state, make_answer_agent


def _detail_state(**kwargs):
    """VECTOR_SEARCH + vector_sub_intent=identification 상태."""
    return make_agent_state(
        intent=IntentType.VECTOR_SEARCH,
        vector_sub_intent="identification",
        **kwargs,
    )


class TestGroupByPlaceName:
    """place_name 그룹핑 단위 테스트 (C)."""

    def test_groups_rows_by_place_name_preserving_order(self):
        rows = [
            {"service_id": "A1", "place_name": "숲속마을 주민공동이용시설"},
            {"service_id": "A2", "place_name": "숲속마을 주민공동이용시설"},
            {"service_id": "B1", "place_name": "생태도서관 숲속애"},
        ]
        focal, others = _group_by_place_name(rows)
        # focal = 첫(RRF 최상위) 결과의 place_name 그룹.
        assert focal["place_name"] == "숲속마을 주민공동이용시설"
        assert [r["service_id"] for r in focal["rows"]] == ["A1", "A2"]
        # 나머지 place_name 은 보조 그룹.
        assert [g["place_name"] for g in others] == ["생태도서관 숲속애"]

    def test_single_place_name_no_other_groups(self):
        rows = [
            {"service_id": "A1", "place_name": "도봉구민회관"},
            {"service_id": "A2", "place_name": "도봉구민회관"},
        ]
        focal, others = _group_by_place_name(rows)
        assert focal["place_name"] == "도봉구민회관"
        assert len(focal["rows"]) == 2
        assert others == []

    def test_empty_rows_returns_none_focal(self):
        focal, others = _group_by_place_name([])
        assert focal is None
        assert others == []

    def test_rows_without_place_name_grouped_under_none(self):
        rows = [{"service_id": "A1", "place_name": None}]
        focal, others = _group_by_place_name(rows)
        assert focal is not None
        assert focal["rows"][0]["service_id"] == "A1"

    def test_focal_first_empty_rows_returns_empty(self):
        """QA 보강: _focal_first([]) 는 focal=None 가드로 빈 입력을 그대로 반환."""
        assert _focal_first([]) == []

    def test_focal_first_single_group_unchanged_order(self):
        """QA 보강: 단일 place_name 이면 입력 순서 그대로."""
        rows = [
            {"service_id": "A1", "place_name": "동일"},
            {"service_id": "A2", "place_name": "동일"},
        ]
        assert [r["service_id"] for r in _focal_first(rows)] == ["A1", "A2"]


class TestDetailPathTrigger:
    """A: 상세형 트리거 조건 — identification 일 때만 분기."""

    async def test_identification_uses_detail_prompt(self):
        agent = make_answer_agent("숲속마을 상세 안내입니다.")
        rows = [
            {
                "service_id": "A1",
                "service_name": "숲속마을 주민공동이용시설 다목적실",
                "place_name": "숲속마을 주민공동이용시설",
                "area_name": "도봉구",
                "service_status": "접수중",
                "payment_type": "유료",
                "target_info": "도봉구민",
                "service_url": "https://yeyak.seoul.go.kr/a1",
            },
            {
                "service_id": "A2",
                "service_name": "숲속마을 주민공동이용시설 회의실",
                "place_name": "숲속마을 주민공동이용시설",
                "area_name": "도봉구",
                "service_status": "접수중",
                "payment_type": "유료",
                "target_info": "도봉구민",
                "service_url": "https://yeyak.seoul.go.kr/a2",
            },
        ]
        state = _detail_state(
            message="도봉구 숲속마을 주민공동이용시설에 대해 알려줘",
            hydrated_services=rows,
        )

        await agent.answer(state)

        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        # 상세형 프롬프트 선택 — 목록형 카드 프롬프트가 아님.
        assert _STRUCT_DETAIL[:30] in system
        assert _STRUCT_CARD_LIST[:30] not in system


class TestSemanticDetailRegressionGuard:
    """회귀 가드: identification 외 경로는 기존 목록형 포맷 유지."""

    async def test_vector_semantic_uses_card_list(self):
        agent = make_answer_agent("목록 안내입니다.")
        rows = [{"service_id": "A1", "service_name": "수영장", "place_name": "센터"}]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            vector_sub_intent="semantic",
            hydrated_services=rows,
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_CARD_LIST[:30] in system
        assert _STRUCT_DETAIL[:30] not in system

    async def test_vector_no_sub_intent_uses_card_list(self):
        agent = make_answer_agent("목록 안내입니다.")
        rows = [{"service_id": "A1", "service_name": "수영장", "place_name": "센터"}]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            hydrated_services=rows,
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_CARD_LIST[:30] in system
        assert _STRUCT_DETAIL[:30] not in system

    async def test_sql_search_uses_card_list_even_if_sub_intent_set(self):
        """SQL_SEARCH 는 identification 신호가 있어도 목록형 유지(트리거는 VECTOR_SEARCH 한정)."""
        agent = make_answer_agent("목록 안내입니다.")
        rows = [{"service_id": "A1", "service_name": "수영장", "place_name": "센터"}]
        state = make_agent_state(
            intent=IntentType.SQL_SEARCH,
            vector_sub_intent="identification",
            sql_results=rows,
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_CARD_LIST[:30] in system
        assert _STRUCT_DETAIL[:30] not in system

    async def test_map_uses_map_prompt(self):
        from agents.answer_agent import _STRUCT_MAP

        agent = make_answer_agent("지도 안내입니다.")
        state = make_agent_state(
            intent=IntentType.MAP,
            vector_sub_intent="identification",
            map_results={
                "features": [
                    {"properties": {"service_id": "M1", "service_name": "체육관"}}
                ]
            },
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_MAP[:30] in system
        assert _STRUCT_DETAIL[:30] not in system

    async def test_analytics_unaffected(self):
        from agents.answer_agent import _STRUCT_ANALYTICS

        agent = make_answer_agent("집계입니다.")
        state = make_agent_state(
            intent=IntentType.ANALYTICS,
            vector_sub_intent="identification",
            analytics_results=[{"group_value": "체육시설", "count": 3}],
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_ANALYTICS[:30] in system
        assert _STRUCT_DETAIL[:30] not in system

    async def test_fallback_unaffected(self):
        from agents.answer_agent import _STRUCT_FALLBACK

        agent = make_answer_agent("폴백입니다.")
        state = make_agent_state(
            intent=IntentType.FALLBACK,
            vector_sub_intent="identification",
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_FALLBACK[:30] in system
        assert _STRUCT_DETAIL[:30] not in system

    async def test_vector_sub_intent_detail_uses_card_list(self):
        """QA 보강: vector_sub_intent='detail' 은 라우터가 실제로 산출하는 값이지만
        identification 이 아니므로 상세형 프롬프트가 발동하지 않고 목록형을 유지한다.
        (트리거가 == 'identification' 정확 일치인지 검증 — 'detail' 오발동 방지)
        """
        agent = make_answer_agent("목록 안내입니다.")
        rows = [{"service_id": "A1", "service_name": "수영장", "place_name": "센터"}]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            vector_sub_intent="detail",
            hydrated_services=rows,
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_CARD_LIST[:30] in system
        assert _STRUCT_DETAIL[:30] not in system


class TestDetailRelaxedNotice:
    """DETAIL 분기는 _build_card_system 을 미경유하므로, 완화 재시도
    (attribute_gap) 결과를 상세형으로 답할 때도 _relaxed_notice 가 system 에
    직접 덧붙는지 단언한다(누락 시 환각·오안내 위험). E2E 회귀를 unit 스코프로 고정.
    """

    def _detail_rows(self):
        return [
            {
                "service_id": "A1",
                "service_name": "마루공원 테니스장",
                "place_name": "마루공원",
                "payment_type": "유료",
                "service_url": "https://yeyak.seoul.go.kr/a1",
            }
        ]

    async def test_detail_relaxed_notice_appended_with_labels(self):
        agent = make_answer_agent("말씀하신 곳은 못 찾았지만 대신 이런 곳은 어떠세요?")
        state = _detail_state(
            message="마루공원 테니스장 무료로 빌릴 수 있어?",
            hydrated_services=self._detail_rows(),
            retry_relaxed=True,
            relaxed_filters=["payment_type", "area_name"],
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        # DETAIL 프롬프트 + 완화 고지 절이 함께 실린다.
        assert _STRUCT_DETAIL[:30] in system
        assert "완화한 결과입니다" in system
        # 드롭 필터 라벨이 정확히 매핑된다.
        assert "요금 조건" in system
        assert "지역" in system
        # 유료→무료 오안내 가드 보존.
        assert "유료 시설을 무료라고 표현하지 마세요" in system

    async def test_detail_no_relaxed_notice_when_not_relaxed(self):
        """retry_relaxed 미설정이면 DETAIL 에 완화 고지가 새지 않는다(오고지 방지)."""
        agent = make_answer_agent("마루공원 테니스장 상세 안내입니다.")
        state = _detail_state(
            message="마루공원 테니스장 알려줘",
            hydrated_services=self._detail_rows(),
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_DETAIL[:30] in system
        assert "완화한 결과입니다" not in system

    async def test_detail_relaxed_notice_generic_when_filters_none(self):
        """relaxed_filters=None(드롭 필터 미상)이라도 일반 완화 문구가 붙는다."""
        agent = make_answer_agent("대신 이런 곳은 어떠세요?")
        state = _detail_state(
            message="마루공원 테니스장",
            hydrated_services=self._detail_rows(),
            retry_relaxed=True,
            relaxed_filters=None,
        )
        await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_DETAIL[:30] in system
        assert "조건을 일부 완화한 결과입니다" in system


class TestDetailFocalOrdering:
    """C: focal 시설 우선 배치 + 보조 목록 분리, truncate 보호."""

    async def test_focal_rows_placed_first_in_results_json(self):
        """focal place_name(첫 결과) 공간들이 results_json 상위에 온다."""
        agent = make_answer_agent("상세 안내입니다.")
        rows = [
            {"service_id": "F1", "service_name": "숲속마을 다목적실", "place_name": "숲속마을"},
            {"service_id": "X1", "service_name": "생태도서관 열람실", "place_name": "생태도서관"},
            {"service_id": "F2", "service_name": "숲속마을 회의실", "place_name": "숲속마을"},
        ]
        state = _detail_state(hydrated_services=rows)

        await agent.answer(state)

        call = agent._answer_chain.ainvoke.call_args[0][0]
        displayed = json.loads(call["results_json"])
        # focal(숲속마을) 두 공간이 보조(생태도서관)보다 먼저 온다.
        place_order = [r["place_name"] for r in displayed]
        assert place_order[0] == "숲속마을"
        assert place_order[1] == "숲속마을"
        assert "생태도서관" in place_order

    async def test_focal_rows_not_truncated_when_many_other_places(self):
        """focal 공간이 _DISPLAY_LIMIT 초과 무관 시설에 밀려 잘리지 않는다."""
        agent = make_answer_agent("상세 안내입니다.")
        rows = [
            {"service_id": "F1", "service_name": "focal 공간1", "place_name": "타깃시설"},
        ]
        # 무관한 다른 place 8건 (DISPLAY_LIMIT=5 초과).
        rows += [
            {"service_id": f"O{i}", "service_name": f"기타{i}", "place_name": f"기타시설{i}"}
            for i in range(8)
        ]
        # focal 의 두 번째 공간을 맨 끝에 배치 — 그룹핑으로 끌어올려져야 한다.
        rows.append(
            {"service_id": "F2", "service_name": "focal 공간2", "place_name": "타깃시설"}
        )
        state = _detail_state(hydrated_services=rows)

        result = await agent.answer(state)

        call = agent._answer_chain.ainvoke.call_args[0][0]
        displayed = json.loads(call["results_json"])
        ids = [r["service_id"] for r in displayed]
        # focal 두 공간 모두 표시분(상위 _DISPLAY_LIMIT)에 포함된다.
        assert "F1" in ids
        assert "F2" in ids
        # 카드도 동일하게 focal 우선.
        card_ids = [c["service_id"] for c in result["service_cards"]]
        assert "F1" in card_ids and "F2" in card_ids

    async def test_service_cards_focal_first(self):
        agent = make_answer_agent("상세 안내입니다.")
        rows = [
            {"service_id": "F1", "service_name": "focal 공간", "place_name": "타깃시설"},
            {"service_id": "X1", "service_name": "기타 공간", "place_name": "기타시설"},
        ]
        state = _detail_state(hydrated_services=rows)
        result = await agent.answer(state)
        cards = result["service_cards"]
        assert cards[0]["service_id"] == "F1"

    async def test_detail_respects_display_limit(self):
        agent = make_answer_agent("상세 안내입니다.")
        rows = [
            {"service_id": f"F{i}", "service_name": f"공간{i}", "place_name": "타깃시설"}
            for i in range(10)
        ]
        state = _detail_state(hydrated_services=rows)
        result = await agent.answer(state)
        assert len(result["service_cards"]) == _DISPLAY_LIMIT

    async def test_focal_group_exceeding_display_limit_fills_display(self):
        """QA 보강: focal 공간이 _DISPLAY_LIMIT(5) 보다 많을 때(6개), 표시분이
        전부 focal 공간으로 채워지고 보조 시설은 슬라이스 밖으로 밀린다."""
        agent = make_answer_agent("상세 안내입니다.")
        # focal 6개 공간 — RRF 순서 사이사이에 보조 시설을 섞어 둔다.
        rows = [
            {"service_id": "F1", "service_name": "공간1", "place_name": "타깃시설"},
            {"service_id": "O1", "service_name": "기타1", "place_name": "기타시설"},
            {"service_id": "F2", "service_name": "공간2", "place_name": "타깃시설"},
            {"service_id": "F3", "service_name": "공간3", "place_name": "타깃시설"},
            {"service_id": "O2", "service_name": "기타2", "place_name": "기타시설"},
            {"service_id": "F4", "service_name": "공간4", "place_name": "타깃시설"},
            {"service_id": "F5", "service_name": "공간5", "place_name": "타깃시설"},
            {"service_id": "F6", "service_name": "공간6", "place_name": "타깃시설"},
        ]
        state = _detail_state(hydrated_services=rows)
        result = await agent.answer(state)
        import json as _json

        displayed = _json.loads(
            agent._answer_chain.ainvoke.call_args[0][0]["results_json"]
        )
        # 표시분 5건이 전부 focal(타깃시설) 공간이며 보조 시설은 밀려난다.
        assert len(displayed) == _DISPLAY_LIMIT
        assert {r["place_name"] for r in displayed} == {"타깃시설"}
        # 카드도 동일하게 focal 우선 5건.
        assert all(c["place_name"] == "타깃시설" for c in result["service_cards"])

    async def test_focal_first_preserves_rrf_order_within_groups(self):
        """QA 보강: focal 그룹 내부 및 보조 그룹의 RRF(입력) 순서가 보존된다."""
        agent = make_answer_agent("상세 안내입니다.")
        rows = [
            {"service_id": "F1", "service_name": "f1", "place_name": "타깃"},
            {"service_id": "O1", "service_name": "o1", "place_name": "기타"},
            {"service_id": "F2", "service_name": "f2", "place_name": "타깃"},
            {"service_id": "O2", "service_name": "o2", "place_name": "기타"},
        ]
        state = _detail_state(hydrated_services=rows)
        result = await agent.answer(state)
        ids = [c["service_id"] for c in result["service_cards"]]
        # focal 먼저(F1,F2 RRF순), 이어서 보조(O1,O2 RRF순).
        assert ids == ["F1", "F2", "O1", "O2"]

    async def test_all_same_place_name_through_answer(self):
        """QA 보강: 전 결과가 동일 place_name 이면 재정렬해도 순서 불변, 상세형 적용."""
        agent = make_answer_agent("상세 안내입니다.")
        rows = [
            {"service_id": "A1", "service_name": "공간A", "place_name": "동일시설"},
            {"service_id": "A2", "service_name": "공간B", "place_name": "동일시설"},
        ]
        state = _detail_state(hydrated_services=rows)
        result = await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_DETAIL[:30] in system
        assert [c["service_id"] for c in result["service_cards"]] == ["A1", "A2"]

    async def test_focal_place_name_none_through_answer(self):
        """QA 보강: focal place_name 이 None 이어도 answer() 가 상세형으로 안전 처리."""
        agent = make_answer_agent("상세 안내입니다.")
        rows = [
            {"service_id": "N1", "service_name": "무명시설1", "place_name": None},
            {"service_id": "N2", "service_name": "무명시설2", "place_name": None},
        ]
        state = _detail_state(hydrated_services=rows)
        result = await agent.answer(state)
        system = agent._answer_chain.ainvoke.call_args[0][0]["system"]
        assert _STRUCT_DETAIL[:30] in system
        ids = [c["service_id"] for c in result["service_cards"]]
        assert ids == ["N1", "N2"]


class TestDetailPromptFields:
    """D: 상세형 프롬프트가 구조화 필드 본문 서술을 지시한다."""

    def test_detail_prompt_instructs_structured_fields(self):
        # 요금/접수기간/대상/공간목록/예약방법을 본문에 서술하라는 취지.
        assert "요금" in _STRUCT_DETAIL
        assert "접수" in _STRUCT_DETAIL
        assert "대상" in _STRUCT_DETAIL
        assert "바로가기" in _STRUCT_DETAIL

    def test_detail_prompt_forbids_fabrication(self):
        # 없는 정보 날조 금지 취지.
        assert "지어내" in _STRUCT_DETAIL or "추측" in _STRUCT_DETAIL

    def test_detail_prompt_focal_centric(self):
        # focal 시설 중심 + 보조 목록 분리 지시.
        assert "보조" in _STRUCT_DETAIL or "이 외" in _STRUCT_DETAIL


class TestDetailEmptyResults:
    """상세형 경로에서 결과 0건이면 안전하게 처리(현행 0건 안내 유지)."""

    async def test_detail_zero_results_still_answers(self):
        agent = make_answer_agent("조건에 맞는 시설을 찾지 못했습니다.")
        state = _detail_state(hydrated_services=[])
        result = await agent.answer(state)
        agent._answer_chain.ainvoke.assert_called_once()
        call = agent._answer_chain.ainvoke.call_args[0][0]
        assert json.loads(call["results_json"]) == []
        assert result["service_cards"] == []


class TestDetailMoreNoticeNeutralized:
    """MINOR 해소: 상세형 경로에서 평면 '외 N건' 지시가 보조 섹션과 경쟁하지 않음.

    overflow(extra_count>0) 상황에서도 상세형은 평면 more_notice 지시를 주입하지
    않고(_more_notice(0) 중립화), overflow 안내는 _STRUCT_DETAIL 보조 목록이 처리한다.
    """

    async def test_detail_focal_overflow_does_not_inject_flat_more_notice(self):
        """focal 6건이 표시 상한을 채워 보조 시설이 잘려도(extra_count>0),
        human 메시지에 평면 '외 N건 반드시 표기' 지시가 들어가지 않는다."""
        agent = make_answer_agent("상세 안내입니다.")
        rows = [
            {"service_id": "F1", "service_name": "공간1", "place_name": "타깃시설"},
            {"service_id": "O1", "service_name": "기타1", "place_name": "기타시설"},
            {"service_id": "F2", "service_name": "공간2", "place_name": "타깃시설"},
            {"service_id": "F3", "service_name": "공간3", "place_name": "타깃시설"},
            {"service_id": "O2", "service_name": "기타2", "place_name": "기타시설"},
            {"service_id": "F4", "service_name": "공간4", "place_name": "타깃시설"},
            {"service_id": "F5", "service_name": "공간5", "place_name": "타깃시설"},
            {"service_id": "F6", "service_name": "공간6", "place_name": "타깃시설"},
        ]
        state = _detail_state(hydrated_services=rows)
        await agent.answer(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        # extra_count>0 이지만 평면 '반드시 표기' 지시는 주입되지 않고 중립 문구가 들어간다.
        assert call["more_notice"] == _more_notice(0)
        assert "반드시 표기" not in call["more_notice"]

    async def test_detail_focal3_plus_aux3_overflow_neutralized(self):
        """focal 3 + 보조 3(총 6건>상한 5) extra_count>0 케이스도 중립화됨."""
        agent = make_answer_agent("상세 안내입니다.")
        rows = [
            {"service_id": "F1", "service_name": "f1", "place_name": "타깃"},
            {"service_id": "F2", "service_name": "f2", "place_name": "타깃"},
            {"service_id": "F3", "service_name": "f3", "place_name": "타깃"},
            {"service_id": "O1", "service_name": "o1", "place_name": "기타A"},
            {"service_id": "O2", "service_name": "o2", "place_name": "기타B"},
            {"service_id": "O3", "service_name": "o3", "place_name": "기타C"},
        ]
        state = _detail_state(hydrated_services=rows)
        await agent.answer(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        assert call["more_notice"] == _more_notice(0)
        assert "반드시 표기" not in call["more_notice"]

    async def test_list_path_still_injects_flat_more_notice(self):
        """회귀 가드: 목록형(비상세) 경로는 기존대로 평면 '외 N건' 지시를 주입한다."""
        agent = make_answer_agent("목록 안내입니다.")
        rows = [
            {"service_id": f"S{i}", "service_name": f"s{i}", "place_name": f"p{i}"}
            for i in range(8)
        ]
        state = make_agent_state(
            intent=IntentType.VECTOR_SEARCH,
            vector_sub_intent="semantic",
            hydrated_services=rows,
        )
        await agent.answer(state)
        call = agent._answer_chain.ainvoke.call_args[0][0]
        assert call["more_notice"] == _more_notice(3)
        assert "외 3건" in call["more_notice"]

    def test_detail_prompt_handles_overflow_in_aux_section(self):
        """(a) 방침: _STRUCT_DETAIL 보조 목록이 overflow('외 N건')를 직접 안내."""
        assert "외 N건" in _STRUCT_DETAIL
        # 평면 꼬리표 금지 취지가 보조 섹션 지시에 포함된다.
        assert "꼬리표" in _STRUCT_DETAIL
