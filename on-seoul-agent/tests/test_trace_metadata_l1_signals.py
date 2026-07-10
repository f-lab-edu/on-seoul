"""_trace_completion_metadata 의 L1 신호 확장 검증.

계약 단일 출처: scripts/eval/l1/extract.py::trace_to_signals + signals.py::QuerySignals.
그래프가 root span metadata 로 emit 하는 키가 추출기가 읽는 키명과 정확히 일치해야
라이브 측정에서 규칙 라벨(THIN/SKEW/ZERO_HIT/RETRIED)이 정확해진다.

확장 신호(키명 = 추출기 계약):
    sql_hits / vector_hits / total_hits — 채널별·총 결과 건수(집계 신호, PII 없음)
    result_quality — thin/skew 자각 dict(pre_answer_gate 산출) passthrough
    forced_intent — 방향성 재시도 강제 intent(enum → str)
    applied_filter_count — router 가 적용한 post-filter 수
    followup_reask — turn_kind(REFINE/DRILL/RELEVANCE) 에서 도출한 후속 재질의 신호

불변식:
    - 기존 키(intent/action/node_path/retry_count/retry_relaxed/cache_hit/error) 회귀 금지.
    - 필드 부재 시 안전(None/기본값), 예외 시 메인 흐름 무영향(best-effort — 별도 커버).
    - PII 금지: 건수·플래그·enum 값만.
"""

from unittest.mock import MagicMock

from agents.graph import _trace_completion_metadata
from schemas.state import IntentType
from scripts.eval.l1.extract import trace_to_signals
from scripts.eval.l1.signals import QuerySignals
from tests.helpers import make_agent_state


# ── 기존 키 회귀 금지 ──


def test_existing_keys_preserved():
    """확장 후에도 기존 7개 키가 그대로 존재한다(추가만, 회귀 금지)."""
    meta = _trace_completion_metadata(make_agent_state())
    for key in (
        "intent",
        "action",
        "turn_kind",
        "node_path",
        "retry_count",
        "retry_relaxed",
        "cache_hit",
        "error",
    ):
        assert key in meta


# ── 신규 신호: 채널 건수 ──


def test_sql_and_vector_hits_counted():
    """sql/vector results 리스트 길이가 sql_hits/vector_hits 로 노출된다."""
    state = make_agent_state(
        sql_results=[{"service_id": "a"}, {"service_id": "b"}, {"service_id": "c"}],
        vector_results=[{"service_id": "x"}],
    )
    meta = _trace_completion_metadata(state)
    assert meta["sql_hits"] == 3
    assert meta["vector_hits"] == 1
    # total_hits = 유효 채널 합.
    assert meta["total_hits"] == 4


def test_hits_none_when_channel_absent():
    """채널이 실행되지 않으면(빈 dict) 해당 hits 는 None(추출기 관대 처리)."""
    meta = _trace_completion_metadata(make_agent_state())
    assert meta["sql_hits"] is None
    assert meta["vector_hits"] is None
    # 어느 채널도 안 돌면 total_hits 도 None(0건과 구별).
    assert meta["total_hits"] is None


def test_total_hits_zero_when_channel_ran_empty():
    """채널이 돌았으나 0건이면 total_hits=0(ZERO_HIT 판정 근거)."""
    state = make_agent_state(sql_results=[])
    meta = _trace_completion_metadata(state)
    assert meta["sql_hits"] == 0
    assert meta["total_hits"] == 0


def test_total_hits_includes_map_and_analytics():
    """map(GeoJSON features)·analytics 결과도 total_hits 에 합산된다."""
    state = make_agent_state(
        map_results={"type": "FeatureCollection", "features": [{}, {}, {}]},
        analytics_results=[{"g": 1}, {"g": 2}],
    )
    meta = _trace_completion_metadata(state)
    assert meta["total_hits"] == 5


# ── 신규 신호: result_quality passthrough ──


def test_result_quality_thin_passthrough():
    """result_quality dict 가 키 변형 없이 그대로 실린다(thin/skew_*)."""
    rq = {"skew_field": None, "skew_value": None, "skew_ratio": None, "thin": True}
    state = make_agent_state(result_quality=rq)
    meta = _trace_completion_metadata(state)
    assert meta["result_quality"] == rq


def test_result_quality_none_when_absent():
    """result_quality 미산출이면 None(현행 조립 그대로)."""
    meta = _trace_completion_metadata(make_agent_state())
    assert meta["result_quality"] is None


# ── 신규 신호: forced_intent ──


def test_forced_intent_serialized_to_value():
    """forced_intent enum 은 .value 문자열로 직렬화된다."""
    state = make_agent_state(forced_intent=IntentType.VECTOR_SEARCH)
    meta = _trace_completion_metadata(state)
    assert meta["forced_intent"] == "VECTOR_SEARCH"


def test_forced_intent_none_when_absent():
    """방향성 재시도가 없으면 forced_intent None."""
    meta = _trace_completion_metadata(make_agent_state())
    assert meta["forced_intent"] is None


# ── 신규 신호: applied_filter_count ──


def test_applied_filter_count_counts_non_null_filters():
    """filters 채널에서 값이 채워진(비-None) 필터 수를 센다."""
    state = make_agent_state(area_name="강남구", service_status="접수중")
    meta = _trace_completion_metadata(state)
    assert meta["applied_filter_count"] == 2


def test_applied_filter_count_ignores_null_filters():
    """None 으로 드롭된 필터는 세지 않는다(완화 재시도 후)."""
    state = make_agent_state()
    state["filters"] = {"area_name": "강남구", "service_status": None}
    meta = _trace_completion_metadata(state)
    assert meta["applied_filter_count"] == 1


def test_applied_filter_count_zero_when_no_filters():
    """필터가 하나도 없으면 0."""
    meta = _trace_completion_metadata(make_agent_state())
    assert meta["applied_filter_count"] == 0


# ── 신규 신호: turn_kind (원본 emit — 분모 스코핑/L2 prior) ──


def test_turn_kind_emitted_as_string():
    """triage.turn_kind(원본 TurnKind)가 문자열로 metadata 에 실린다."""
    state = make_agent_state()
    state["triage"] = {"turn_kind": "DRILL"}
    meta = _trace_completion_metadata(state)
    assert meta["turn_kind"] == "DRILL"


def test_turn_kind_enum_serialized_to_value():
    """turn_kind 가 enum 형태여도 .value 로 직렬화된다(방어)."""
    state = make_agent_state()
    state["triage"] = {"turn_kind": MagicMock(value="META")}
    meta = _trace_completion_metadata(state)
    assert meta["turn_kind"] == "META"


def test_turn_kind_none_when_absent():
    """turn_kind 미설정(빈 triage)이면 None(구 트레이스 하위호환)."""
    meta = _trace_completion_metadata(make_agent_state())
    assert meta["turn_kind"] is None


def test_turn_kind_roundtrip_scopes_non_retrieve():
    """graph 가 emit 한 turn_kind=META 를 추출기가 읽어 NON_RETRIEVE 로 스코핑한다."""
    state = make_agent_state(message="왜 그 결과야")
    state["triage"] = {"action": MagicMock(value="RETRIEVE"), "turn_kind": "META"}
    meta = _trace_completion_metadata(state)
    signals = trace_to_signals(
        trace_id="rt-meta", span=_FakeSpan(meta, state["message"])
    )
    assert signals.turn_kind == "META"
    assert signals.is_non_retrieve() is True


# ── 신규 신호: followup_reask (turn_kind 도출) ──


def test_followup_reask_true_for_refine_turn():
    """turn_kind=REFINE 은 세션 내 후속 재질의 → followup_reask=True."""
    state = make_agent_state()
    state["triage"] = {"turn_kind": "REFINE"}
    meta = _trace_completion_metadata(state)
    assert meta["followup_reask"] is True


def test_followup_reask_true_for_drill_and_relevance():
    """DRILL/RELEVANCE 도 직전 결과 대상 후속 턴 → followup_reask=True."""
    for kind in ("DRILL", "RELEVANCE"):
        state = make_agent_state()
        state["triage"] = {"turn_kind": kind}
        meta = _trace_completion_metadata(state)
        assert meta["followup_reask"] is True, kind


def test_followup_reask_false_for_new_turn():
    """turn_kind=NEW(신규 질문)면 followup_reask=False."""
    state = make_agent_state()
    state["triage"] = {"turn_kind": "NEW"}
    meta = _trace_completion_metadata(state)
    assert meta["followup_reask"] is False


def test_followup_reask_false_when_turn_kind_absent():
    """turn_kind 미설정(빈 triage)이면 followup_reask=False(안전 기본)."""
    meta = _trace_completion_metadata(make_agent_state())
    assert meta["followup_reask"] is False


# ── 라운드트립: emit → extract.trace_to_signals 가 실제 값으로 소비 ──


class _FakeSpan:
    """root "chat" span observation 을 모사(input/metadata 는 span 에 실림)."""

    def __init__(self, meta: dict, query: str) -> None:
        self.id = "obs-rt-1"
        self.trace_id = "rt-1"
        self.type = "SPAN"
        self.name = "chat"
        self.parent_observation_id = None
        self.input = query
        self.output = None
        self.metadata = meta


def test_roundtrip_extractor_reads_extended_signals_as_real_values():
    """graph 가 emit 한 metadata 를 extract.trace_to_signals 가 None 이 아닌
    실제 값으로 읽는다(계약 일치 증명 — 갭 닫힘)."""
    state = make_agent_state(
        message="강남 무료 수영장",
        sql_results=[{"service_id": "a"}, {"service_id": "b"}],
        area_name="강남구",
        forced_intent=IntentType.SQL_SEARCH,
        result_quality={"thin": True, "skew_field": None, "skew_ratio": None},
    )
    state["triage"] = {"action": MagicMock(value="RETRIEVE"), "turn_kind": "REFINE"}
    state["plan"] = {"intent": MagicMock(value="SQL_SEARCH")}
    state["retry_count"] = 1

    meta = _trace_completion_metadata(state)
    signals: QuerySignals = trace_to_signals(
        trace_id="rt-1", span=_FakeSpan(meta, state["message"])
    )

    # 추출기가 확장 신호를 실제 값으로 읽는다(더 이상 None 관대 처리 아님).
    assert signals.sql_hits == 2
    assert signals.total_hits == 2
    assert signals.forced_intent == "SQL_SEARCH"
    assert signals.applied_filter_count == 1
    assert signals.retry_count == 1
    assert signals.followup_reask is True
    # 규칙 라벨 판정 헬퍼가 정확히 동작.
    assert signals.is_thin() is True
    assert signals.was_retried() is True
    assert signals.is_zero_hit() is False


def test_roundtrip_zero_hit_detectable():
    """0건 채널이 total_hits=0 으로 실려 추출기 is_zero_hit()=True 로 판정된다."""
    state = make_agent_state(message="은평구 심야 승마", sql_results=[])
    meta = _trace_completion_metadata(state)
    signals = trace_to_signals(
        trace_id="rt-1", span=_FakeSpan(meta, state["message"])
    )
    assert signals.total_hits == 0
    assert signals.is_zero_hit() is True
