"""L1 측정 — critic-on Langfuse 트레이스에서 critic 동작 지표 산출.

배포 후(enable_retrieval_critic=true) 실 트래픽 트레이스를 분석해 세 질문에 답한다:
  1. critic 이 실제로 복구를 늘리나? (REPLAN 복구율)
  2. 지연 얼마나 늘리나?           (critic 스팬 소요 + 진입/미진입 총 지연 비교)
  3. 80% 경로 보존되나?            (RETRIEVE 중 critic 미진입 = 명백히 좋음 비율)

관측 계약(재사용 — 신규 인프라 없음):
  · root "chat" span metadata: intent/action/turn_kind/retry_count/total_hits/
    result_quality/... (extract.py 계약, agents/graph.py::_trace_completion_metadata).
  · critic 자식 span(name="retrieval_critic") metadata: {entry_signal(zero/thin/skew),
    decision(ANSWER/REPLAN/STOP), round} (agents/graph.py::record_critic_span).
    라운드마다 스팬이 하나씩 열린다. critic 미발동 트레이스는 자식 스팬이 없다.
  · 타이밍: ObservationsView.latency(초) 또는 start_time/end_time(ISO8601). 있으면 쓰고,
    없으면 명시적으로 "타이밍 신호 없음"으로 흡수한다(관대 원칙).

extract.py 계약 재사용: pick_root_span(root "chat"), pick_critic_spans(자식 스팬),
trace_to_signals(root metadata → QuerySignals). 이 모듈은 그 위에 critic 지표만 얹는다.

RETRIEVE 분모 스코핑(측정 타당성): critic 은 검색을 시도한 턴에서만 발동하므로 모든
지표는 QuerySignals.is_non_retrieve() 가 False 인 트레이스만 분모로 쓴다.

결정은 하지 않는다 — 스크립트는 수치까지만 산출한다(사람의 롤아웃 게이트 입력).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from pydantic import BaseModel, Field

from scripts.eval.l1.extract import (
    _DictTrace,
    pick_critic_spans,
    pick_root_span,
    trace_to_signals,
)
from scripts.eval.l1.signals import QuerySignals

_CRITIC_FIXTURE_DEFAULT = Path(__file__).parent / "fixtures" / "critic_traces.json"

# critic 결정 3택 + 관측 실패 시 None(fail-open) 을 "미결정"으로 버킷.
_DECISIONS = ("ANSWER", "REPLAN", "STOP")
_UNDECIDED = "UNDECIDED"


class CriticRound(BaseModel):
    """critic 라운드 하나(자식 span metadata) — {entry_signal, decision, round} + 지연."""

    entry_signal: str | None = Field(default=None, description="zero/thin/skew")
    decision: str | None = Field(default=None, description="ANSWER/REPLAN/STOP 또는 None")
    round: int | None = Field(default=None, description="critic 라운드 인덱스")
    latency_s: float | None = Field(default=None, description="critic 스팬 소요(초)")


class CriticTrace(BaseModel):
    """한 트레이스의 critic 관점 구조 — root 최종 신호 + critic 라운드들.

    critic 미발동(자식 스팬 없음)은 rounds=[] 로 구분된다(entered() 참고).
    """

    signals: QuerySignals
    rounds: list[CriticRound] = Field(default_factory=list)
    total_latency_s: float | None = Field(
        default=None, description="root 'chat' span 총 소요(초) — 있으면 총 지연 비교용"
    )

    def entered(self) -> bool:
        """critic 진입 여부 — 자식 스팬(라운드)이 하나라도 있으면 진입."""
        return len(self.rounds) > 0

    def had_replan(self) -> bool:
        return any(r.decision == "REPLAN" for r in self.rounds)

    def critic_latency_s(self) -> float | None:
        """이 트레이스 critic 스팬 소요 합(초) — 타이밍 있는 라운드만. 없으면 None."""
        vals = [r.latency_s for r in self.rounds if r.latency_s is not None]
        return sum(vals) if vals else None

    def recovered(self) -> bool:
        """최종 결과가 양호한가 — total_hits>0 且 thin 아님(root 최종 metadata 기준)."""
        s = self.signals
        return s.total_hits is not None and s.total_hits > 0 and not s.is_thin()


class CriticMetrics(BaseModel):
    """critic 동작 지표 리포트 — 롤아웃 게이트 입력(수치만)."""

    total: int = Field(description="전체 트레이스 수")
    retrieval_total: int = Field(description="검색 시도 RETRIEVE 트레이스(모든 지표 분모)")
    non_retrieve_total: int = Field(default=0)

    critic_entered: int = Field(description="critic 진입(자식 스팬 존재) 트레이스 수")
    activation_rate: float = Field(description="critic 발동율 = entered/retrieval_total")
    path_preserved: int = Field(description="critic 미진입(명백히 좋음) RETRIEVE 수")
    path_preserved_rate: float = Field(description="80% 경로 보존율 = preserved/retrieval_total")

    # decision 분포 — critic 스팬(라운드) 단위 카운트.
    round_total: int = Field(default=0, description="critic 라운드(스팬) 총 수")
    decision_counts: dict[str, int] = Field(default_factory=dict)

    # REPLAN 복구율 — REPLAN 을 낸 트레이스 중 최종 양호로 끝난 비율.
    replan_traces: int = Field(default=0, description="REPLAN 을 최소 1회 낸 트레이스 수")
    replan_recovered: int = Field(default=0, description="그 중 최종 결과 양호")
    replan_recovery_rate: float | None = Field(
        default=None, description="복구율 = recovered/replan_traces (REPLAN 없으면 None)"
    )
    entry_signal_counts: dict[str, int] = Field(
        default_factory=dict, description="escalation 진입 신호 분포(라운드 단위)"
    )

    # 지연 — 타이밍 신호가 있는 만큼만.
    critic_latency_samples: int = Field(default=0, description="critic 스팬 소요 표본 수")
    critic_latency_mean_s: float | None = None
    critic_latency_median_s: float | None = None
    entered_total_latency_mean_s: float | None = Field(
        default=None, description="critic 진입 트레이스 총 지연 평균(초)"
    )
    not_entered_total_latency_mean_s: float | None = Field(
        default=None, description="critic 미진입 RETRIEVE 트레이스 총 지연 평균(초)"
    )


# ── 트레이스 → CriticTrace 구조화 (extract 계약 위) ────────────────────────


def _parse_ts(val: Any) -> datetime | None:
    """ISO8601 문자열/naive datetime 을 datetime 으로 파싱(관대 — 실패 시 None)."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str) and val:
        try:
            # langfuse 는 'Z' 접미(UTC)로 줄 수 있어 fromisoformat 호환 처리.
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _span_latency(span: Any) -> float | None:
    """관측 span 의 소요(초) — latency 우선, 없으면 end_time-start_time. 없으면 None."""
    lat = getattr(span, "latency", None)
    if isinstance(lat, (int, float)):
        return float(lat)
    start = _parse_ts(getattr(span, "start_time", None))
    end = _parse_ts(getattr(span, "end_time", None))
    if start is not None and end is not None:
        delta = (end - start).total_seconds()
        return delta if delta >= 0 else None
    return None


def _critic_round(span: Any) -> CriticRound:
    meta: dict[str, Any] = getattr(span, "metadata", None) or {}
    return CriticRound(
        entry_signal=meta.get("entry_signal"),
        decision=meta.get("decision"),
        round=meta.get("round") if isinstance(meta.get("round"), int) else None,
        latency_s=_span_latency(span),
    )


def trace_to_critic(trace: Any) -> CriticTrace:
    """트레이스 상세(observations 중첩)를 CriticTrace 로 구조화한다.

    root "chat" span → QuerySignals(최종 신호), 자식 "retrieval_critic" span 들 →
    CriticRound 리스트(round 오름차순). critic 미발동이면 rounds=[].
    """
    root = pick_root_span(trace)
    signals = trace_to_signals(trace_id=getattr(trace, "id", ""), span=root)
    rounds = [_critic_round(s) for s in pick_critic_spans(trace)]
    return CriticTrace(
        signals=signals,
        rounds=rounds,
        total_latency_s=_span_latency(root) if root is not None else None,
    )


def load_fixture_critic_traces(path: Path | None = None) -> list[CriticTrace]:
    """번들(또는 지정) critic 픽스처를 CriticTrace 리스트로 로드한다(드라이런).

    라이브와 동일 구조(트레이스 → observations 중첩, root "chat" + 자식
    "retrieval_critic" span)를 써서 같은 추출 경로(pick_root_span/pick_critic_spans →
    trace_to_critic)를 통과한다 — 계약을 진짜로 검증한다.
    """
    fixture_path = path or _CRITIC_FIXTURE_DEFAULT
    raw = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    return [trace_to_critic(_DictTrace(item)) for item in raw]


def fetch_live_critic_traces(
    *,
    days: int,
    limit: int = 500,
    trace_name: str = "chat",
) -> list[CriticTrace]:
    """라이브 Langfuse 에서 최근 N일 트레이스를 조회해 CriticTrace 로 구조화한다.

    자격증명은 settings(langfuse_public_key/secret_key/host)에서 읽는다. 키 미설정이면
    RuntimeError 로 명확히 실패시킨다(사람이 자격증명 주입 후 라이브 실행). 실제 네트워크
    I/O 라 단위 테스트에서 호출하지 않는다(드라이런 픽스처로 파이프라인 증명).

    extract.fetch_live_traces 와 동일한 trace.list → trace.get 경로를 쓰되, root 신호뿐
    아니라 critic 자식 스팬까지 읽어야 하므로 detail 객체(observations 중첩)를 그대로
    trace_to_critic 에 넘긴다.
    """
    from langfuse import Langfuse

    from core.config import settings

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        raise RuntimeError(
            "Langfuse 키 미설정 — LANGFUSE_PUBLIC_KEY/SECRET_KEY 를 .env 로 주입한 뒤 "
            "라이브 추출을 실행하세요. 자격증명 없이 검증하려면 --dry-run 을 쓰세요."
        )

    from datetime import timedelta, timezone

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    from_ts = datetime.now(timezone.utc) - timedelta(days=days)

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

    out: list[CriticTrace] = []
    for trace_id in trace_ids:
        detail = client.api.trace.get(trace_id)
        out.append(trace_to_critic(detail))
    return out


# ── 지표 산출 ──────────────────────────────────────────────────────────────


def compute_metrics(traces: list[CriticTrace]) -> CriticMetrics:
    """CriticTrace 리스트에서 critic 동작 지표를 산출한다(RETRIEVE 분모 스코핑)."""
    total = len(traces)
    retrieval = [t for t in traces if not t.signals.is_non_retrieve()]
    r_total = len(retrieval)
    non_retrieve = total - r_total

    entered = [t for t in retrieval if t.entered()]
    not_entered = [t for t in retrieval if not t.entered()]
    n_entered = len(entered)

    # decision / entry_signal 분포 — 라운드(스팬) 단위.
    decision_counts: dict[str, int] = {d: 0 for d in _DECISIONS}
    decision_counts[_UNDECIDED] = 0
    entry_counts: dict[str, int] = {}
    round_total = 0
    for t in entered:
        for rnd in t.rounds:
            round_total += 1
            key = rnd.decision if rnd.decision in _DECISIONS else _UNDECIDED
            decision_counts[key] += 1
            if rnd.entry_signal:
                entry_counts[rnd.entry_signal] = entry_counts.get(rnd.entry_signal, 0) + 1
    decision_counts = {k: v for k, v in decision_counts.items() if v}

    # REPLAN 복구율.
    replan_traces = [t for t in entered if t.had_replan()]
    replan_recovered = [t for t in replan_traces if t.recovered()]
    recovery_rate = (
        len(replan_recovered) / len(replan_traces) if replan_traces else None
    )

    # 지연 — critic 스팬 소요(타이밍 있는 트레이스만).
    critic_lats = [
        lat for t in entered if (lat := t.critic_latency_s()) is not None
    ]
    entered_totals = [t.total_latency_s for t in entered if t.total_latency_s is not None]
    not_entered_totals = [
        t.total_latency_s for t in not_entered if t.total_latency_s is not None
    ]

    return CriticMetrics(
        total=total,
        retrieval_total=r_total,
        non_retrieve_total=non_retrieve,
        critic_entered=n_entered,
        activation_rate=(n_entered / r_total) if r_total else 0.0,
        path_preserved=len(not_entered),
        path_preserved_rate=(len(not_entered) / r_total) if r_total else 0.0,
        round_total=round_total,
        decision_counts=decision_counts,
        replan_traces=len(replan_traces),
        replan_recovered=len(replan_recovered),
        replan_recovery_rate=recovery_rate,
        entry_signal_counts=entry_counts,
        critic_latency_samples=len(critic_lats),
        critic_latency_mean_s=(mean(critic_lats) if critic_lats else None),
        critic_latency_median_s=(median(critic_lats) if critic_lats else None),
        entered_total_latency_mean_s=(mean(entered_totals) if entered_totals else None),
        not_entered_total_latency_mean_s=(
            mean(not_entered_totals) if not_entered_totals else None
        ),
    )


def format_report(m: CriticMetrics) -> str:
    """지표를 사람이 읽는 리포트 문자열로 포맷(stdout 출력용)."""

    def _pct(x: float) -> str:
        return f"{100 * x:5.1f}%"

    lines = [
        "=== L1 critic 동작 지표 (critic-on 트레이스) ===",
        f"총 트레이스: {m.total}  "
        f"(RETRIEVE: {m.retrieval_total}, NON_RETRIEVE: {m.non_retrieve_total})",
        "",
        "[발동 / 경로 보존]  (분모 = RETRIEVE)",
        f"  critic 발동율          {m.critic_entered:5d} / {m.retrieval_total} "
        f"({_pct(m.activation_rate)})",
        f"  80% 경로 보존(미진입)  {m.path_preserved:5d} / {m.retrieval_total} "
        f"({_pct(m.path_preserved_rate)})",
        "",
        f"[decision 분포]  (critic 라운드 {m.round_total}개 단위)",
    ]
    if m.decision_counts:
        for k, v in sorted(m.decision_counts.items(), key=lambda kv: -kv[1]):
            pct = v / m.round_total if m.round_total else 0
            lines.append(f"  {k:12s} {v:5d}  ({_pct(pct)})")
    else:
        lines.append("  (critic 발동 트레이스 없음)")
    lines.append("")
    lines.append("[진입 신호 분포]  (escalation 원인, 라운드 단위)")
    if m.entry_signal_counts:
        for k, v in sorted(m.entry_signal_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {k:12s} {v:5d}")
    else:
        lines.append("  (없음)")
    lines.append("")
    lines.append("[REPLAN 복구율]  (재탐색이 실제로 복구했나 — 최종 total_hits>0 且 thin 아님)")
    if m.replan_recovery_rate is not None:
        lines.append(
            f"  REPLAN 트레이스 {m.replan_traces}건 중 최종 양호 {m.replan_recovered}건 "
            f"→ 복구율 {_pct(m.replan_recovery_rate)}"
        )
    else:
        lines.append("  (REPLAN 을 낸 트레이스 없음)")
    lines.append("")
    lines.append("[추가 지연]  (타이밍 신호 있는 만큼만)")
    if m.critic_latency_samples:
        lines.append(
            f"  critic 스팬 소요: 표본 {m.critic_latency_samples}건, "
            f"평균 {m.critic_latency_mean_s:.2f}s, 중앙값 {m.critic_latency_median_s:.2f}s"
        )
    else:
        lines.append("  critic 스팬 소요: 타이밍 신호 없음")
    if (
        m.entered_total_latency_mean_s is not None
        and m.not_entered_total_latency_mean_s is not None
    ):
        delta = m.entered_total_latency_mean_s - m.not_entered_total_latency_mean_s
        lines.append(
            f"  총 지연 평균: 진입 {m.entered_total_latency_mean_s:.2f}s vs "
            f"미진입 {m.not_entered_total_latency_mean_s:.2f}s (차이 {delta:+.2f}s)"
        )
    else:
        lines.append("  총 지연 비교: 타이밍 신호 부족(진입/미진입 중 한쪽 결측)")
    lines.append("")
    lines.append("※ 롤아웃 판단(계속/확대/롤백)은 사람 몫 — 이 리포트는 수치만 제공한다.")
    return "\n".join(lines)
