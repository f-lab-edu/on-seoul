"""Langfuse 트레이스 추출 → QuerySignals.

라이브 경로(fetch_live_traces)와 드라이런 경로(load_fixture_traces)를 모두 제공한다.
자격증명은 env/settings 로만 주입하며(하드코딩 금지), 라이브 접근 불가 환경에서는
번들 픽스처로 end-to-end 파이프라인을 증명한다.

Langfuse root span metadata 계약(갭 닫힘):
  그래프(agents/graph.py::_trace_completion_metadata)가 아래 신호를 모두 root span
  metadata 로 노출한다 — 이 추출기가 읽는 키명과 정확히 일치한다:
    intent/action/node_path/retry_count/retry_relaxed/cache_hit/error,
    sql_hits/vector_hits/total_hits, result_quality(thin/skew_field/skew_ratio),
    forced_intent, applied_filter_count, followup_reask.
  따라서 실 트래픽 트레이스에서 규칙 라벨(ZERO_HIT/THIN/SKEW/RETRIED)이 실제 값으로
  결정된다. 신호가 없는 구(舊) 트레이스는 여전히 None/기본값으로 관대하게 흡수한다
  (fx-old-1 픽스처가 이 하위호환을 커버).

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


def trace_to_signals(trace: Any) -> QuerySignals:
    """단일 Langfuse 트레이스(객체 또는 dict-유사)를 QuerySignals 로 정규화한다."""
    meta: dict[str, Any] = getattr(trace, "metadata", None) or {}

    return QuerySignals(
        trace_id=str(getattr(trace, "id", "")),
        raw_query=_extract_query(getattr(trace, "input", None)),
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


class _DictTrace:
    """dict 픽스처를 trace_to_signals 가 기대하는 속성 접근 형태로 감싼다."""

    def __init__(self, d: dict[str, Any]) -> None:
        self.id = d.get("id")
        self.input = d.get("input")
        self.output = d.get("output")
        self.metadata = d.get("metadata")


def load_fixture_traces(path: Path | None = None) -> list[QuerySignals]:
    """번들(또는 지정) JSON 픽스처를 QuerySignals 리스트로 로드한다(드라이런)."""
    fixture_path = path or _FIXTURE_DEFAULT
    raw = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    return [trace_to_signals(_DictTrace(item)) for item in raw]


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

    signals: list[QuerySignals] = []
    page = 1
    while len(signals) < limit:
        resp = client.api.trace.list(
            name=trace_name,
            from_timestamp=from_ts,
            page=page,
            limit=min(100, limit - len(signals)),
        )
        data = getattr(resp, "data", []) or []
        if not data:
            break
        signals.extend(trace_to_signals(t) for t in data)
        page += 1

    return signals[:limit]
