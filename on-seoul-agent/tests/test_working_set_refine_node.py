"""working_set_refine_node 단위 테스트 (P1-3) — 신규 제약 머지 + forced_intent.

검증:
- prev applied_filters{area_name:강남} + 이번 발화 신규 제약(payment_type=무료)을
  RouterAgent 로 정제 → effective filters 에 둘 다 머지(신규 우선).
- forced_intent = prev_working_set.intent (intent 는 prev 로 고정, classify 산출 무시).
- prev intent 미상 → forced_intent 미세팅(router 가 분류).
- 빈 워킹셋 → 필터/forced 없음.
"""

from unittest.mock import AsyncMock, MagicMock

from agents.nodes.intake import IntakeNodes
from agents.router_agent import RouterAgent
from schemas.state import IntentType
from tests.helpers import make_intake, make_router


def _node(router) -> IntakeNodes:
    return IntakeNodes(intake=make_intake(), router=router)


def _failing_router(exc: Exception = RuntimeError("LLM 500")) -> RouterAgent:
    """classify 가 항상 exc 를 던지는 RouterAgent mock(다운스트림 실패 경로)."""
    agent = RouterAgent.__new__(RouterAgent)
    structured = MagicMock()
    structured.ainvoke = AsyncMock(side_effect=exc)
    llm = MagicMock()
    llm.with_structured_output = MagicMock(return_value=structured)
    agent._llm = llm
    return agent


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
        router = make_router(IntentType.VECTOR_SEARCH, refined_query="강남 무료 수영")
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
        assert update["node_path"] == [
            "working_set_refine",
            "working_set_refine:no_base",
        ]

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


class TestRefineTopicCarryover:
    """REFINE 후속이 직전 토픽을 잃고 환각 필터로 검색하는 버그(B/C/폴백) 회귀."""

    async def test_filter_only_followup_preserves_prev_refined_query(self):
        """(a) prev refined_query 유지 + 이번 발화 델타(접수중)만 추가, 환각 필터 0.

        "접수중인곳으로 다시 찾아줘"는 순수 필터 추가 후속이다. 직전 토픽
        refined_query="주말 가족 문화행사"가 유지되고, 발화에 없는 max_class_name(체육
        시설)/area_name(마포구)은 절대 끼지 않으며, service_status=접수중만 추가된다.
        """
        # 환각 시나리오를 재현: router 가 (history bleed 시) 체육시설/마포구를 산출하려
        # 해도, B(history 미전달)로 이번 발화 근거 필터(service_status=접수중)만 남아야
        # 한다. fake router 는 이번 발화만 본 결과를 흉내 내 service_status 만 산출한다.
        router = make_router(IntentType.SQL_SEARCH, service_status="접수중")
        ws = {
            "intent": IntentType.SQL_SEARCH,
            "refined_query": "주말 가족 문화행사",
            "applied_filters": {},
        }
        from tests.helpers import make_agent_state

        state = make_agent_state(
            message="접수중인곳으로 다시 찾아줘",
            prev_working_set=ws,
            history=[
                {
                    "role": "user",
                    "content": "주말에 가족과 참여할만한 문화행사 있을까?",
                },
                {"role": "assistant", "content": "문화행사 N건을 찾았어요..."},
            ],
        )
        update = await _node(router).working_set_refine_node(state)
        # 환각 필터(체육시설/마포구)가 없다.
        assert "max_class_name" not in update["filters"]
        assert "area_name" not in update["filters"]
        # 이번 발화 진짜 델타만 추가.
        assert update["filters"] == {"service_status": "접수중"}
        # 직전 토픽 refined_query 유지(이번 발화는 새 토픽을 도입하지 않음).
        assert update["plan"]["refined_query"] == "주말 가족 문화행사"

    async def test_router_called_without_history(self):
        """B: _parse_new_constraints 는 history 없이 router 를 호출(bleed 차단)."""
        router = make_router(IntentType.SQL_SEARCH, service_status="접수중")
        ws = {
            "intent": IntentType.SQL_SEARCH,
            "refined_query": "주말 가족 문화행사",
            "applied_filters": {},
        }
        from tests.helpers import make_agent_state

        state = make_agent_state(
            message="접수중인곳으로 다시 찾아줘",
            prev_working_set=ws,
            history=[{"role": "user", "content": "주말 가족 문화행사 있을까?"}],
        )
        await _node(router).working_set_refine_node(state)
        # router.classify(message, history=...) 호출 시 history 인자가 비어 있어야 한다.
        structured = router._llm.with_structured_output.return_value
        call = structured.ainvoke.call_args
        # 호출 메시지에 직전 발화 텍스트가 합성되지 않았는지 확인.
        sent = call.args[0] if call.args else call.kwargs.get("input")
        joined = "".join(getattr(m, "content", "") for m in sent)
        assert "주말 가족 문화행사" not in joined

    async def test_no_base_uses_history_search_turn(self):
        """(b) prev_working_set 비었을 때 history 직전 검색 발화를 토픽 base 로.

        no_base 인데 history 에 직전 검색 "주말 가족 문화행사"가 있으면 그 토픽을
        base 로 삼고, 발화에 없는 필터는 만들지 않는다.
        """
        # fake router: 폴백이 history 검색 발화를 base 로 재정제 → refined_query 산출.
        router = make_router(IntentType.SQL_SEARCH, refined_query="주말 가족 문화행사")
        from tests.helpers import make_agent_state

        state = make_agent_state(
            message="접수중인곳으로 다시 찾아줘",
            prev_working_set={},
            history=[
                {
                    "role": "user",
                    "content": "주말에 가족과 참여할만한 문화행사 있을까?",
                },
                {"role": "assistant", "content": "문화행사 N건을 찾았어요..."},
            ],
        )
        update = await _node(router).working_set_refine_node(state)
        # history 토픽 base 가 refined_query 로 운반된다.
        assert update["plan"]["refined_query"] == "주말 가족 문화행사"
        # 환각 필터 없음.
        assert "max_class_name" not in update.get("filters", {})
        assert "area_name" not in update.get("filters", {})

    def test_last_search_user_turn_skips_thin_followup(self):
        """_last_search_user_turn: 빈약한 필터-추가 후속은 건너뛰고 검색 발화를 고른다."""
        from agents.nodes.intake import _last_search_user_turn

        history = [
            {"role": "user", "content": "주말에 가족과 참여할만한 문화행사 있을까?"},
            {"role": "assistant", "content": "문화행사 N건..."},
            {"role": "user", "content": "접수중인곳으로 다시 찾아줘"},
        ]
        assert (
            _last_search_user_turn(history)
            == "주말에 가족과 참여할만한 문화행사 있을까?"
        )

    def test_last_search_user_turn_none_when_no_search(self):
        from agents.nodes.intake import _last_search_user_turn

        assert _last_search_user_turn([{"role": "user", "content": "다시"}]) is None
        assert _last_search_user_turn([]) is None

    async def test_real_delta_payment_type_survives(self):
        """(c) "그 중 무료만" 진짜 델타(payment_type=무료)는 정상 채택(회귀 가드)."""
        router = make_router(IntentType.SQL_SEARCH, payment_type="무료")
        ws = {
            "intent": IntentType.SQL_SEARCH,
            "refined_query": "강남 수영장",
            "applied_filters": {"area_name": "강남구"},
        }
        from tests.helpers import make_agent_state

        state = make_agent_state(
            message="그 중 무료만",
            prev_working_set=ws,
            history=[{"role": "user", "content": "강남 수영장 알려줘"}],
        )
        update = await _node(router).working_set_refine_node(state)
        assert update["filters"]["payment_type"] == "무료"
        assert update["filters"]["area_name"] == "강남구"
        # 직전 토픽 유지.
        assert update["plan"]["refined_query"] == "강남 수영장"


class TestRouterFailureBestEffort:
    """router.classify(LLM/네트워크) 실패 시 base 필터만으로 REFINE 흐름을 잇는다."""

    async def test_router_failure_keeps_base_filters(self):
        """다운스트림 실패(LLM 500) — 신규 제약은 비지만 base 필터/forced 는 유지된다."""
        router = _failing_router()
        ws = {
            "intent": IntentType.SQL_SEARCH,
            "refined_query": "강남 수영장",
            "applied_filters": {"area_name": "강남구"},
        }
        from tests.helpers import make_agent_state

        state = make_agent_state(message="그 중 무료만", prev_working_set=ws)
        update = await _node(router).working_set_refine_node(state)
        # 신규 제약 추출은 실패해 빈 dict → base 필터만 남는다(REFINE 흐름 비차단).
        assert update["filters"] == {"area_name": "강남구"}
        # 직전 토픽 보존(C)은 LLM 실패와 무관하게 prev_refined 로 유지된다.
        assert update["plan"]["refined_query"] == "강남 수영장"
        assert update["forced_intent"] == IntentType.SQL_SEARCH

    async def test_router_failure_in_no_base_fallback_no_crash(self):
        """no_base 폴백에서 router 실패해도 예외 없이 base 필터 없는 update 를 낸다."""
        router = _failing_router()
        from tests.helpers import make_agent_state

        state = make_agent_state(
            message="접수중인곳으로 다시 찾아줘",
            prev_working_set={},
            history=[{"role": "user", "content": "주말 가족 문화행사 있을까?"}],
        )
        update = await _node(router).working_set_refine_node(state)
        # 실패 → 신규 제약 0, refined_query None → plan/filters 키 미세팅.
        assert "filters" not in update
        assert "plan" not in update
        assert "working_set_refine:no_base" in update["node_path"]


class TestLastSearchUserTurnHeuristic:
    """_last_search_user_turn 폴백 휴리스틱 — 보장과 한계를 명시적으로 핀(QA 추가).

    이 휴리스틱은 *안전 경계가 아니라* no_base 일 때의 토픽 연속성 best-effort 다.
    환각 필터 차단의 안전 경계는 B(history 미전달)가 담당하며, 폴백이 토픽을
    오선택해도 router 가 합성 문자열에서 발화에 없는 필터를 만들지는 않는다.
    아래 테스트는 (ii) 정상 검색을 놓치지 않음을 보장으로, (i)(iii) 오선택을
    문서화된 한계로 핀한다.
    """

    def _f(self):
        from agents.nodes.intake import _last_search_user_turn

        return _last_search_user_turn

    def test_picks_short_normal_search_turn(self):
        """(ii) 보장 — 힌트 없는 짧은(>=6자) 정상 검색 발화는 선택된다."""
        f = self._f()
        assert f([{"role": "user", "content": "수영장 알려줘"}]) == "수영장 알려줘"

    def test_skips_assistant_turns(self):
        """assistant 발화는 토픽 base 후보가 아니다(user 발화만 본다)."""
        f = self._f()
        history = [
            {"role": "user", "content": "주말 가족 문화행사 있을까?"},
            {"role": "assistant", "content": "접수중인 무료 행사 N건을 찾았어요 다시"},
        ]
        assert f(history) == "주말 가족 문화행사 있을까?"

    def test_picks_most_recent_qualifying_turn(self):
        """여러 검색 발화 중 *가장 최근* 적격 발화를 고른다(역순 순회)."""
        f = self._f()
        history = [
            {"role": "user", "content": "강남 수영장 알려줘"},
            {"role": "assistant", "content": "N건..."},
            {"role": "user", "content": "마포 도서관 대관 알려줘"},
        ]
        assert f(history) == "마포 도서관 대관 알려줘"

    def test_too_short_search_turn_is_missed_limitation(self):
        """한계 — 6자 미만 검색 발화("요가")는 놓친다(보수적 임계의 대가)."""
        f = self._f()
        assert f([{"role": "user", "content": "요가"}]) is None

    def test_chitchat_misfire_limitation(self):
        """(i) 한계 — 힌트 토큰 없는 잡담/META user 발화는 토픽으로 오선택될 수 있다.

        "고마워 정말 도움이 됐어"는 검색이 아니지만 힌트 토큰이 없고 6자 이상이라
        base 로 집힌다. 안전 경계가 아님을 핀(node 레벨에서 환각 필터 0 은 별도
        보장 — test_chitchat_base_does_not_fabricate_filters 참고).
        """
        f = self._f()
        assert f([{"role": "user", "content": "고마워 정말 도움이 됐어"}]) == (
            "고마워 정말 도움이 됐어"
        )

    def test_long_refine_misfire_limitation(self):
        """(iii) 한계 — 힌트 토큰이 있어도 18자 이상이면 refine 발화가 base 로 집힌다.

        "그 중에서 무료인 것만 다시 보여줄래"는 refine 인데 길이 임계를 넘겨
        토픽 base 로 오선택된다. 합쳐도 둘 다 델타라 환각으로 번지진 않는다.
        """
        f = self._f()
        assert (
            f([{"role": "user", "content": "그 중에서 무료인 것만 다시 보여줄래"}])
            == "그 중에서 무료인 것만 다시 보여줄래"
        )

    async def test_chitchat_base_does_not_fabricate_filters(self):
        """안전 경계 핀 — 폴백이 잡담을 base 로 오선택해도 환각 필터는 0.

        no_base + history 직전 user 발화가 잡담("고마워...")이어도, router 는
        합성 문자열("고마워... 접수중인곳으로 다시")에서 이번 발화 근거 델타
        (service_status=접수중)만 산출하고 area_name/max_class_name 은 만들지 않는다.
        (fake router 는 이번 발화 근거 필터만 흉내 — B 의 효과를 모사.)
        """
        router = make_router(IntentType.SQL_SEARCH, service_status="접수중")
        from tests.helpers import make_agent_state

        state = make_agent_state(
            message="접수중인곳으로 다시 찾아줘",
            prev_working_set={},
            history=[{"role": "user", "content": "고마워 정말 도움이 됐어"}],
        )
        update = await _node(router).working_set_refine_node(state)
        assert update.get("filters", {}) == {"service_status": "접수중"}
        assert "area_name" not in update.get("filters", {})
        assert "max_class_name" not in update.get("filters", {})
        assert "working_set_refine:no_base" in update["node_path"]
