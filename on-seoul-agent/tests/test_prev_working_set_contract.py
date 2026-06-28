"""대화 워킹셋(P1) 크로스턴 계약 — ChatRequest 주입 + emit 라운드트립 + 평면 폴백.

검증(P1):
- ChatRequest.prev_working_set(신규 채널) → AgentState.prev_working_set 주입.
- 평면 슬롯(prev_entities/prev_intent/prev_reasoning) 폴백(하위호환).
- 미전송 → None(현행 100% 보존).
- emit: final 의 prev_working_set 이 effective(완화 후) 필터를 캡처(P1-4) →
  다음 턴 ChatRequest 로 라운드트립.
"""

from routers.chat import _build_prev_working_set, _emit_working_set
from schemas.chat import ChatRequest, PrevWorkingSetPayload
from schemas.state import IntentType


class TestPrevWorkingSetInjection:
    def test_nested_channel_preferred(self):
        req = ChatRequest(
            room_id=1,
            message_id=2,
            message="그 중 무료만",
            prev_working_set=PrevWorkingSetPayload(
                entities=[{"service_id": "S1", "label": "강남 수영장"}],
                intent=IntentType.SQL_SEARCH,
                reasoning="수영장 검색",
                refined_query="강남 수영장",
                applied_filters={"area_name": "강남구"},
                relaxed=True,
                relaxed_filters=["payment_type"],
            ),
        )
        ws = _build_prev_working_set(req)
        assert ws is not None
        assert ws["entities"] == [{"service_id": "S1", "label": "강남 수영장"}]
        assert ws["intent"] == IntentType.SQL_SEARCH
        assert ws["refined_query"] == "강남 수영장"
        assert ws["applied_filters"] == {"area_name": "강남구"}
        assert ws["relaxed"] is True
        assert ws["relaxed_filters"] == ["payment_type"]

    def test_flat_fallback_when_nested_absent(self):
        req = ChatRequest(
            room_id=1,
            message_id=2,
            message="후속",
            prev_entities=[{"service_id": "S9", "label": "테니스장"}],
            prev_intent=IntentType.VECTOR_SEARCH,
            prev_reasoning="직전 근거",
        )
        ws = _build_prev_working_set(req)
        assert ws is not None
        assert ws["entities"] == [{"service_id": "S9", "label": "테니스장"}]
        assert ws["intent"] == IntentType.VECTOR_SEARCH
        assert ws["reasoning"] == "직전 근거"
        # 평면 폴백엔 신규 필드 없음.
        assert ws["refined_query"] is None
        assert ws["applied_filters"] == {}
        assert ws["relaxed"] is False

    def test_none_when_no_carryover(self):
        req = ChatRequest(room_id=1, message_id=1, message="첫 턴")
        assert _build_prev_working_set(req) is None


class TestEmitWorkingSet:
    def test_captures_effective_filters_and_entities(self):
        # result 는 최종 AgentState. filters 채널 = effective(완화 후) 필터.
        result = {
            "plan": {"intent": IntentType.SQL_SEARCH, "refined_query": "강남 수영장"},
            "filters": {"area_name": "강남구", "payment_type": None},
            "output": {
                "service_cards": [
                    {"service_id": "S1", "service_name": "강남 수영장"},
                    {"service_id": "S2", "service_name": "역삼 수영장"},
                ]
            },
            "triage": {"user_rationale": "강남 수영장 검색"},
            "retry_relaxed": True,
            "relaxed_filters": ["payment_type"],
        }
        ws = _emit_working_set(result)
        assert ws["intent"] == "SQL_SEARCH"
        assert ws["refined_query"] == "강남 수영장"
        # effective 필터만(None 드롭).
        assert ws["applied_filters"] == {"area_name": "강남구"}
        assert ws["entities"] == [
            {"service_id": "S1", "label": "강남 수영장"},
            {"service_id": "S2", "label": "역삼 수영장"},
        ]
        assert ws["relaxed"] is True
        assert ws["relaxed_filters"] == ["payment_type"]

    def test_roundtrip_emit_to_next_request(self):
        """emit 한 prev_working_set 이 다음 턴 ChatRequest 로 재주입된다(계약 정합)."""
        result = {
            "plan": {"intent": IntentType.SQL_SEARCH, "refined_query": "q"},
            "filters": {"area_name": "마포구"},
            "output": {"service_cards": [{"service_id": "S1", "service_name": "L1"}]},
            "triage": {"user_rationale": "r"},
            "retry_relaxed": False,
            "relaxed_filters": [],
        }
        emitted = _emit_working_set(result)
        # 다음 턴: Spring 이 emitted 를 그대로 ChatRequest.prev_working_set 으로 회신.
        next_req = ChatRequest(
            room_id=1,
            message_id=3,
            message="그 중 무료만",
            prev_working_set=PrevWorkingSetPayload(**emitted),
        )
        ws = _build_prev_working_set(next_req)
        assert ws["intent"] == IntentType.SQL_SEARCH
        assert ws["applied_filters"] == {"area_name": "마포구"}
        assert ws["entities"] == [{"service_id": "S1", "label": "L1"}]


class TestEmitWorkingSetCarryForward:
    """버그 D — 비검색 턴이 멀티턴 워킹셋(carryover)을 지우지 않는다.

    비검색/무결과 턴(META/EXPLAIN, 결과 없는 DIRECT_ANSWER/AMBIGUOUS/
    domain_outside)은 새 검색 레시피를 만들지 않으므로, 빈 워킹셋으로 직전
    레시피를 덮지 않고 들어온 prev_working_set 을 carry-forward 한다.
    """

    # 직전 검색(예: turn 192)이 남긴 워킹셋 — 다음 비검색 턴이 보존해야 한다.
    PREV_WS = {
        "entities": [{"service_id": "S1", "label": "가족 문화행사"}],
        "intent": IntentType.VECTOR_SEARCH,
        "reasoning": "가족과 갈만한 문화행사",
        "refined_query": "주말 가족 문화행사",
        "applied_filters": {"area_name": "강남구"},
        "relaxed": False,
        "relaxed_filters": [],
    }

    def test_search_turn_uses_this_turn_result(self):
        """(a) 검색 턴(plan.intent + cards) → result 기반 워킹셋(현행) 유지."""
        result = {
            "plan": {"intent": IntentType.SQL_SEARCH, "refined_query": "강남 수영장"},
            "filters": {"area_name": "강남구"},
            "output": {"service_cards": [{"service_id": "S2", "service_name": "수영장"}]},
            "triage": {"action": "RETRIEVE", "turn_kind": "NEW"},
            "prev_working_set": self.PREV_WS,
        }
        ws = _emit_working_set(result)
        assert ws["intent"] == "SQL_SEARCH"
        assert ws["refined_query"] == "강남 수영장"
        assert ws["entities"] == [{"service_id": "S2", "label": "수영장"}]

    def test_meta_turn_carries_forward_prev(self):
        """(b) META/EXPLAIN 턴 → 들어온 prev_working_set 을 그대로 carry-forward."""
        # explain_node 는 output.answer 만 세팅. plan.intent / service_cards 없음.
        result = {
            "plan": {},
            "filters": {},
            "output": {"answer": "가족과 갈만하다고 판단한 이유는..."},
            "triage": {"action": "EXPLAIN", "turn_kind": "META"},
            "prev_working_set": self.PREV_WS,
        }
        ws = _emit_working_set(result)
        assert ws["refined_query"] == "주말 가족 문화행사"
        assert ws["applied_filters"] == {"area_name": "강남구"}
        assert ws["intent"] == IntentType.VECTOR_SEARCH
        # entities 도 직전 유지(빈 배열로 덮지 않음).
        assert ws["entities"] == [{"service_id": "S1", "label": "가족 문화행사"}]

    def test_domain_outside_carries_forward_prev(self):
        """(c) 결과 없는 OUT_OF_SCOPE(domain_outside) 턴도 carry-forward."""
        result = {
            "plan": {},
            "filters": {},
            "output": {"answer": "서비스 범위를 벗어납니다."},
            "triage": {
                "action": "OUT_OF_SCOPE",
                "turn_kind": "NEW",
                "out_of_scope_type": "domain_outside",
            },
            "prev_working_set": self.PREV_WS,
        }
        ws = _emit_working_set(result)
        assert ws["refined_query"] == "주말 가족 문화행사"
        assert ws["applied_filters"] == {"area_name": "강남구"}
        assert ws["entities"] == [{"service_id": "S1", "label": "가족 문화행사"}]

    def test_no_recipe_turn_without_prev_returns_empty(self):
        """(d) 첫 턴(prev_working_set 없음) + 비검색 턴 → 기존(빈) 동작."""
        result = {
            "plan": {},
            "filters": {},
            "output": {"answer": "안녕하세요"},
            "triage": {"action": "DIRECT_ANSWER", "turn_kind": "NEW"},
            "prev_working_set": None,
        }
        ws = _emit_working_set(result)
        assert ws["refined_query"] is None
        assert ws["applied_filters"] == {}
        assert ws["entities"] == []
        assert ws["intent"] is None

    def test_drill_turn_with_cards_uses_this_turn(self):
        """결과 카드를 새로 보인 DRILL 등 결과 턴은 result 기반 생성(carry 아님)."""
        result = {
            "plan": {},  # rehydrate/describe 경로는 plan.intent 를 안 쓸 수 있다.
            "filters": {},
            "output": {
                "service_cards": [{"service_id": "S1", "service_name": "가족 문화행사"}]
            },
            "triage": {"action": "RETRIEVE", "turn_kind": "DRILL"},
            "prev_working_set": self.PREV_WS,
        }
        ws = _emit_working_set(result)
        # 카드가 있으므로 result 기반(현행) — refined_query 는 이번 plan(없음)에서.
        assert ws["entities"] == [{"service_id": "S1", "label": "가족 문화행사"}]
        assert ws["refined_query"] is None

    def test_scenario_search_meta_keeps_topic(self):
        """(e) 검색 → META 2턴: META 가 emit 하는 워킹셋 refined_query 가 검색 토픽 유지."""
        # turn 192 검색 emit
        search_result = {
            "plan": {"intent": IntentType.VECTOR_SEARCH, "refined_query": "주말 가족 문화행사"},
            "filters": {"area_name": "강남구"},
            "output": {
                "service_cards": [{"service_id": "S1", "service_name": "가족 문화행사"}]
            },
            "triage": {"action": "RETRIEVE", "turn_kind": "NEW"},
            "prev_working_set": None,
        }
        ws192 = _emit_working_set(search_result)
        assert ws192["refined_query"] == "주말 가족 문화행사"

        # turn 194 META — Spring 이 ws192 를 prev_working_set 으로 회신.
        meta_result = {
            "plan": {},
            "filters": {},
            "output": {"answer": "이유는..."},
            "triage": {"action": "EXPLAIN", "turn_kind": "META"},
            "prev_working_set": ws192,
        }
        ws194 = _emit_working_set(meta_result)
        # 196 이 받게 될 워킹셋의 토픽이 직전 검색으로 유지된다(빈 base 회귀 방지).
        assert ws194["refined_query"] == "주말 가족 문화행사"
        assert ws194["applied_filters"] == {"area_name": "강남구"}
        assert ws194["entities"] == [{"service_id": "S1", "label": "가족 문화행사"}]


class TestEmitWorkingSetCarryForwardNodeShapes:
    """버그 D 회귀 — *실제 노드 산출 dict 형태*로 carry-forward 판정을 검증한다.

    QA 발견: 기존 carry-forward 테스트는 비검색 턴 result 를 plan={} 로 모델링하나,
    direct_answer_node 는 실제로 plan.intent=FALLBACK 을 dict_merge 채널에 기록한다.
    produced_recipe = (intent is not None) 판정은 이 FALLBACK 을 '레시피 생성'으로
    오인해 carry-forward 를 건너뛰고 직전 워킹셋을 빈 값으로 덮는다(바로 버그 D 의
    증상). 노드의 실제 반환 shape 로 회귀를 고정한다.
    """

    PREV_WS = {
        "entities": [{"service_id": "S1", "label": "가족 문화행사"}],
        "intent": IntentType.VECTOR_SEARCH,
        "reasoning": "가족과 갈만한 문화행사",
        "refined_query": "주말 가족 문화행사",
        "applied_filters": {"area_name": "강남구"},
        "relaxed": False,
        "relaxed_filters": [],
    }

    def test_direct_answer_node_shape_carries_forward(self):
        """결과 없는 DIRECT_ANSWER — direct_answer_node 는 plan.intent=FALLBACK 을
        반환한다(answer.py:48). 이 turn 은 새 검색 레시피가 아니므로 직전 워킹셋을
        carry-forward 해야 한다(덮어쓰면 안 됨)."""
        result = {
            # direct_answer_node 의 실제 반환(dict_merge 후 result["plan"]).
            "plan": {"intent": IntentType.FALLBACK},
            "filters": {},
            "output": {"answer": "안녕하세요", "service_cards": None},
            "triage": {"action": "DIRECT_ANSWER", "turn_kind": "NEW"},
            "prev_working_set": self.PREV_WS,
        }
        ws = _emit_working_set(result)
        assert ws["refined_query"] == "주말 가족 문화행사"
        assert ws["applied_filters"] == {"area_name": "강남구"}
        assert ws["entities"] == [{"service_id": "S1", "label": "가족 문화행사"}]

    def test_explain_fallback_to_direct_answer_carries_forward(self):
        """prev_reasoning 없는 EXPLAIN 은 explain_node 가 direct_answer_node 로
        위임(answer.py:174)하므로 plan.intent=FALLBACK 으로 끝난다. 역시 carry."""
        result = {
            "plan": {"intent": IntentType.FALLBACK},
            "filters": {},
            "output": {"answer": "안내드립니다", "service_cards": None},
            "triage": {"action": "EXPLAIN", "turn_kind": "META"},
            "prev_working_set": self.PREV_WS,
        }
        ws = _emit_working_set(result)
        assert ws["refined_query"] == "주말 가족 문화행사"
        assert ws["entities"] == [{"service_id": "S1", "label": "가족 문화행사"}]

    def test_ambiguous_clarify_node_shape_carries_forward(self):
        """결과 없는 AMBIGUOUS — ambiguous_node(answer.py:83) 는 plan 을 덮지 않고
        output.answer(되물음)만 세팅, service_cards=None. 검색 intent·카드 없으므로
        carry-forward 해야 한다. (membership 룰의 무결과 비검색 경로 매트릭스 보강.)"""
        result = {
            # ambiguous_node 는 plan override 가 없음 — triage 의 비검색 상태 그대로.
            "plan": {},
            "filters": {},
            "output": {"answer": "어느 지역을 찾으시나요?", "service_cards": None},
            "triage": {"action": "AMBIGUOUS", "turn_kind": "NEW"},
            "prev_working_set": self.PREV_WS,
        }
        ws = _emit_working_set(result)
        assert ws["refined_query"] == "주말 가족 문화행사"
        assert ws["applied_filters"] == {"area_name": "강남구"}
        assert ws["entities"] == [{"service_id": "S1", "label": "가족 문화행사"}]

    def test_oos_attribute_gap_node_shape_generates_recipe(self):
        """버그 D 역방향 회귀 — attribute_gap/operational_detail OUT_OF_SCOPE 는
        domain_outside 와 달리 plan.intent=VECTOR_SEARCH 를 세팅(answer.py:144)하고
        실제 식별 검색을 수행한다. 이는 *진짜 검색 레시피*이므로 carry 가 아니라
        result 기반 생성이어야 한다(직전 워킹셋을 덮는 게 정상). membership 룰이
        VECTOR_SEARCH 를 검색으로 인정하는지 고정한다."""
        result = {
            "plan": {
                "intent": IntentType.VECTOR_SEARCH,
                "vector_sub_intent": "attribute_gap",
                "refined_query": "한강공원 주차 가능 여부",
            },
            "filters": {"area_name": "영등포구"},
            "output": {
                "service_cards": [
                    {"service_id": "S9", "service_name": "한강공원 주차장"}
                ]
            },
            "triage": {
                "action": "OUT_OF_SCOPE",
                "turn_kind": "NEW",
                "out_of_scope_type": "attribute_gap",
            },
            "prev_working_set": self.PREV_WS,
        }
        ws = _emit_working_set(result)
        # 이번 턴 검색 레시피로 갱신 — 직전(VECTOR/강남구/S1) 을 carry 하지 않는다.
        assert ws["intent"] == "VECTOR_SEARCH"
        assert ws["refined_query"] == "한강공원 주차 가능 여부"
        assert ws["applied_filters"] == {"area_name": "영등포구"}
        assert ws["entities"] == [{"service_id": "S9", "label": "한강공원 주차장"}]
