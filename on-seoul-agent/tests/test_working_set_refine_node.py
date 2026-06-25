"""working_set_refine_node 단위 테스트 (P1-3) — 신규 제약 머지 + forced_intent.

검증:
- prev applied_filters{area_name:강남} + 이번 발화 신규 제약(payment_type=무료)을
  RouterAgent 로 정제 → effective filters 에 둘 다 머지(신규 우선).
- forced_intent = prev_working_set.intent (intent 는 prev 로 고정, classify 산출 무시).
- prev intent 미상 → forced_intent 미세팅(router 가 분류).
- 빈 워킹셋 → 필터/forced 없음.
"""

from agents.nodes.intake import IntakeNodes
from schemas.state import IntentType
from tests.helpers import make_intake, make_router


def _node(router) -> IntakeNodes:
    return IntakeNodes(intake=make_intake(), router=router)


class TestWorkingSetRefine:
    async def test_merges_new_constraint_with_base_filters(self):
        """직전 area_name=강남구 + 이번 발화 신규 payment_type=무료 → 둘 다 effective."""
        # 이번 발화("그 중 무료만")에서 router 가 신규 제약 payment_type=무료 추출.
        router = make_router(IntentType.SQL_SEARCH, payment_type="무료")
        ws = {
            "entities": [{"service_id": "S1", "label": "강남 수영장"}],
            "intent": IntentType.SQL_SEARCH,
            "applied_filters": {"area_name": "강남구"},
        }
        from tests.helpers import make_agent_state

        state = make_agent_state(message="그 중 무료만", prev_working_set=ws)
        update = await _node(router).working_set_refine_node(state)
        # base(area_name=강남구) + 신규(payment_type=무료) 둘 다 머지.
        assert update["filters"] == {"area_name": "강남구", "payment_type": "무료"}
        # intent 는 prev 로 고정.
        assert update["forced_intent"] == IntentType.SQL_SEARCH
        assert update["node_path"] == ["working_set_refine"]

    async def test_new_constraint_overrides_base(self):
        """동일 키 충돌 시 이번 발화 신규 제약이 base 를 덮는다."""
        router = make_router(IntentType.SQL_SEARCH, area_name="마포구")
        ws = {
            "intent": IntentType.SQL_SEARCH,
            "applied_filters": {"area_name": "강남구"},
        }
        from tests.helpers import make_agent_state

        state = make_agent_state(message="마포구로 바꿔줘", prev_working_set=ws)
        update = await _node(router).working_set_refine_node(state)
        assert update["filters"]["area_name"] == "마포구"

    async def test_refined_query_carried_to_plan(self):
        """router 가 산출한 refined_query 가 plan 채널로 흐른다(재검색 질의 반영)."""
        router = make_router(
            IntentType.VECTOR_SEARCH, refined_query="강남 무료 수영"
        )
        ws = {
            "intent": IntentType.VECTOR_SEARCH,
            "applied_filters": {"area_name": "강남구"},
        }
        from tests.helpers import make_agent_state

        state = make_agent_state(message="그 중 무료만", prev_working_set=ws)
        update = await _node(router).working_set_refine_node(state)
        assert update["plan"]["refined_query"] == "강남 무료 수영"

    async def test_drops_none_base_filters(self):
        router = make_router(IntentType.VECTOR_SEARCH, service_status="접수중")
        ws = {
            "intent": IntentType.VECTOR_SEARCH,
            "applied_filters": {"area_name": "마포구", "payment_type": None},
        }
        from tests.helpers import make_agent_state

        state = make_agent_state(message="접수중인 것만", prev_working_set=ws)
        update = await _node(router).working_set_refine_node(state)
        assert update["filters"] == {"area_name": "마포구", "service_status": "접수중"}
        assert update["forced_intent"] == IntentType.VECTOR_SEARCH

    async def test_no_prev_intent_no_forced(self):
        router = make_router(IntentType.SQL_SEARCH, payment_type="무료")
        ws = {"applied_filters": {"area_name": "강남구"}}
        from tests.helpers import make_agent_state

        state = make_agent_state(message="무료만", prev_working_set=ws)
        update = await _node(router).working_set_refine_node(state)
        assert "forced_intent" not in update
        # 신규 제약은 prev intent 미상이어도 머지된다.
        assert update["filters"] == {"area_name": "강남구", "payment_type": "무료"}

    async def test_empty_working_set_still_parses_new(self):
        """빈 워킹셋이어도 이번 발화 신규 제약은 추출돼 filters 로 흐른다."""
        router = make_router(IntentType.SQL_SEARCH, payment_type="무료")
        from tests.helpers import make_agent_state

        state = make_agent_state(message="무료만", prev_working_set=None)
        update = await _node(router).working_set_refine_node(state)
        assert update["filters"] == {"payment_type": "무료"}
        assert "forced_intent" not in update
        # base 가 없으므로(prev_working_set=None) no_base breadcrumb 가 남는다.
        assert update["node_path"] == ["working_set_refine", "working_set_refine:no_base"]

    async def test_no_base_breadcrumb_when_prev_working_set_empty(self):
        """REFINE 인데 base 필터가 없으면 no_base breadcrumb 가 trace 에 남는다(관측).

        prev_working_set 공백(base 없음)이면 머지 결과가 신규 제약만 남아 사실상
        NEW 동작이다. turn_kind=REFINE 분류였음에도 carryover 베이스가 없었음을
        node_path 에 기록해 degrade 를 자각 가능하게 한다.
        """
        router = make_router(IntentType.SQL_SEARCH)  # 신규 제약도 없음
        from tests.helpers import make_agent_state

        state = make_agent_state(message="그것들", prev_working_set={})
        update = await _node(router).working_set_refine_node(state)
        assert "working_set_refine:no_base" in update["node_path"]

    async def test_no_no_base_breadcrumb_when_base_present(self):
        """base 필터가 있으면 no_base breadcrumb 는 남지 않는다."""
        router = make_router(IntentType.SQL_SEARCH, payment_type="무료")
        ws = {
            "intent": IntentType.SQL_SEARCH,
            "applied_filters": {"area_name": "강남구"},
        }
        from tests.helpers import make_agent_state

        state = make_agent_state(message="그 중 무료만", prev_working_set=ws)
        update = await _node(router).working_set_refine_node(state)
        assert "working_set_refine:no_base" not in update["node_path"]

    async def test_no_new_constraints_keeps_base_only(self):
        """이번 발화에서 신규 제약이 없으면 base 필터만 흐른다(no-op 머지)."""
        router = make_router(IntentType.SQL_SEARCH)  # 신규 필터 없음
        ws = {
            "intent": IntentType.SQL_SEARCH,
            "applied_filters": {"area_name": "강남구"},
        }
        from tests.helpers import make_agent_state

        state = make_agent_state(message="그것들", prev_working_set=ws)
        update = await _node(router).working_set_refine_node(state)
        assert update["filters"] == {"area_name": "강남구"}
        assert update["forced_intent"] == IntentType.SQL_SEARCH
