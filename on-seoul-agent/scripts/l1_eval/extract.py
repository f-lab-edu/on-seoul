"""Langfuse 트레이스 추출 → QuerySignals.

라이브 경로(fetch_live_traces)와 드라이런 경로(load_fixture_traces)를 모두 제공한다.
자격증명은 env/settings 로만 주입하며(하드코딩 금지), 라이브 접근 불가 환경에서는
번들 픽스처로 end-to-end 파이프라인을 증명한다.

Langfuse observation(root span) metadata 계약:
  그래프(agents/graph.py::_langfuse_trace)는 질의·완료 메타데이터를 트레이스 레벨이
  아니라 root "chat" SPAN observation 에 싣는다:
    - start_as_current_observation(as_type="span", name="chat", input=message) — input 이 span 에.
    - root_span.update(output=..., metadata=_trace_completion_metadata(...)) — metadata 가 span 에.
    - propagate_attributes(trace_name="chat", session_id=...) — 트레이스엔 이름/세션ID만.
  Langfuse v4(OTel)에서 trace.input/metadata 는 비어 있을 수 있고(내용은 observation 에
  존재), trace.list 요약은 트레이스 레벨 필드만 준다. 따라서 추출기는 트레이스별로
  상세를 조회해(client.api.trace.get → observations 중첩) root "chat" span 을 골라 그
  span 의 input/metadata/output 을 읽는다. trace_id 는 트레이스에서 가져온다
  (observation.id ≠ trace_id).

  root span metadata 는 추출기가 읽는 키명과 정확히 일치한다:
    intent/action/node_path/retry_count/retry_relaxed/cache_hit/error,
    sql_hits/vector_hits/total_hits, result_quality(thin/skew_field/skew_ratio),
    forced_intent, applied_filter_count, followup_reask.
  따라서 실 트래픽 트레이스에서 규칙 라벨(ZERO_HIT/THIN/SKEW/RETRIED)이 실제 값으로
  결정된다. root span 이 없거나(구 트레이스) 신호가 빠진 경우 None/기본값으로 관대하게
  흡수한다(fx-old-1 픽스처가 하위호환을 커버).

  followup_reask 는 전용 state 슬롯이 아니라 turn_kind(REFINE/DRILL/RELEVANCE)에서
  도출한 근사 신호다(그래프 측 _followup_reask 참고). 프로덕션 트레이스가 여전히
  담지 않을 수 있는 필드는 None/False 로 두고 라벨러가 NORMAL 로 흡수한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts.l1_eval.signals import QuerySignals

_FIXTURE_DEFAULT = Path(__file__).parent / "fixtures" / "sample_traces.json"
_ROOT_SPAN_NAME = "chat"


def _extract_query(trace_input: Any) -> str:
    """트레이스 input 에서 사용자 원본 질의를 뽑는다.

    graph 는 root span input=state["message"](문자열)로 넣지만, 다른 경로/버전에서
    dict({"message": ...}) 형태일 수 있어 둘 다 지원한다.
    """
    if isinstance(trace_input, str):
        return trace_input
    if isinstance(trace_input, dict):
        for key in ("message", "query", "input"):
            val = trace_input.get(key)
            if isinstance(val, str):
                return val
    return ""


def trace_to_signals(*, trace_id: str, span: Any) -> QuerySignals:
    """root "chat" span observation 을 QuerySignals 로 정규화한다.

    trace_id 는 트레이스에서(observation.id 아님), 나머지 신호는 span 의
    input/metadata 에서 읽는다. span 이 None(root span 미검출 — 구 트레이스/누락)이면
    trace_id 만 유지하고 신호는 전부 기본값(라벨러가 NORMAL 로 흡수).
    """
    meta: dict[str, Any] = getattr(span, "metadata", None) or {}

    return QuerySignals(
        trace_id=str(trace_id or ""),
        raw_query=_extract_query(getattr(span, "input", None)),
        intent=meta.get("intent"),
        action=meta.get("action"),
        sql_hits=meta.get("sql_hits"),
        vector_hits=meta.get("vector_hits"),
        total_hits=meta.get("total_hits"),
        result_quality=meta.get("result_quality"),
        retry_count=int(meta.get("retry_count") or 0),
        forced_intent=meta.get("forced_intent"),
        followup_reask=bool(meta.get("followup_reask", False)),
        applied_filter_count=meta.get("applied_filter_count"),
    )


def pick_root_span(trace: Any, *, span_name: str = _ROOT_SPAN_NAME) -> Any | None:
    """트레이스 상세(observations 중첩)에서 root "chat" span observation 을 고른다.

    선택 우선순위:
      1. type=SPAN 이고 name 이 정확히 일치하는 observation(그래프가 붙인 root span).
      2. (폴백) parent_observation_id 가 없는 최상위 SPAN — 이름 규약이 바뀐 경우 대비.
    관대 원칙: 못 찾으면 None(추출기가 신호를 기본값으로 흡수). observations 가 없거나
    조회 실패로 비어 있어도 None.
    """
    observations = getattr(trace, "observations", None) or []

    def _is_span(o: Any) -> bool:
        t = getattr(o, "type", None)
        return t is None or str(t).upper() == "SPAN"

    for o in observations:
        if _is_span(o) and getattr(o, "name", None) == span_name:
            return o
    for o in observations:
        if _is_span(o) and not getattr(o, "parent_observation_id", None):
            return o
    return None


class _ObsView:
    """dict 픽스처의 observation 을 pick_root_span/trace_to_signals 가 기대하는
    속성 접근 형태로 감싼다(langfuse ObservationsView 부분 모방)."""

    def __init__(self, d: dict[str, Any]) -> None:
        self.id = d.get("id")
        self.trace_id = d.get("trace_id")
        self.type = d.get("type", "SPAN")
        self.name = d.get("name")
        self.parent_observation_id = d.get("parent_observation_id")
        self.input = d.get("input")
        self.output = d.get("output")
        self.metadata = d.get("metadata")


class _DictTrace:
    """dict 픽스처를 pick_root_span 이 기대하는 트레이스(observations 중첩) 형태로 감싼다."""

    def __init__(self, d: dict[str, Any]) -> None:
        self.id = d.get("id")
        self.observations = [_ObsView(o) for o in (d.get("observations") or [])]


def load_fixture_traces(path: Path | None = None) -> list[QuerySignals]:
    """번들(또는 지정) JSON 픽스처를 QuerySignals 리스트로 로드한다(드라이런).

    픽스처는 라이브와 동일 구조(트레이스 → observations 중첩, root "chat" span 에
    input/metadata)를 쓴다 — 드라이런과 라이브가 같은 추출 경로(pick_root_span →
    trace_to_signals)를 통과해 계약을 진짜로 검증한다.
    """
    fixture_path = path or _FIXTURE_DEFAULT
    raw = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    out: list[QuerySignals] = []
    for item in raw:
        trace = _DictTrace(item)
        out.append(trace_to_signals(trace_id=trace.id, span=pick_root_span(trace)))
    return out


def fetch_live_traces(
    *,
    days: int,
    limit: int = 500,
    trace_name: str = "chat",
) -> list[QuerySignals]:
    """라이브 Langfuse 에서 최근 N일 트레이스를 페이지네이션 조회한다.

    자격증명은 settings(langfuse_public_key/secret_key/host)에서 읽는다. langfuse 가
    비활성이거나 키 미설정이면 RuntimeError 로 명확히 실패시킨다(사람이 자격증명을 주입해
    라이브 실행하도록 유도). 이 함수는 실제 네트워크 I/O 라 단위 테스트에서 호출하지 않는다.
    """
    from langfuse import Langfuse

    from core.config import settings

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        raise RuntimeError(
            "Langfuse 키 미설정 — LANGFUSE_PUBLIC_KEY/SECRET_KEY 를 .env 로 주입한 뒤 "
            "라이브 추출을 실행하세요. 자격증명 없이 검증하려면 --fixture 드라이런을 쓰세요."
        )

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    from_ts = datetime.now(timezone.utc) - timedelta(days=days)

    # 1) trace.list 로 최근 N일 "chat" 트레이스 ID 를 페이지네이션 수집(요약만 — 트레이스
    #    레벨 input/metadata 는 비어 있을 수 있어 신호 추출엔 못 씀).
    trace_ids: list[str] = []
    page = 1
    while len(trace_ids) < limit:
        resp = client.api.trace.list(
            name=trace_name,
            from_timestamp=from_ts,
            page=page,
            limit=min(100, limit - len(trace_ids)),
        )
        data = getattr(resp, "data", []) or []
        if not data:
            break
        trace_ids.extend(str(t.id) for t in data if getattr(t, "id", None))
        page += 1

    trace_ids = trace_ids[:limit]

    # 2) 트레이스별 상세 조회(client.api.trace.get → observations 중첩) 후 root "chat"
    #    span 을 골라 신호를 뽑는다. 트레이스당 1회 상세 조회(N+1)는 배치 도구라 허용.
    signals: list[QuerySignals] = []
    for trace_id in trace_ids:
        detail = client.api.trace.get(trace_id)
        span = pick_root_span(detail, span_name=trace_name)
        signals.append(trace_to_signals(trace_id=trace_id, span=span))

    return signals
