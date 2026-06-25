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
