"""사람 검증 하네스 + 자동↔사람 일치율 + 최종 버킷 분포 집계.

흐름:
  1. sample_for_review — 자동 라벨된 항목에서 50~100건을 결정적(seed)으로 샘플.
  2. export_human_review — csv/json 으로 내보내 사람이 human_bucket 열을 채우게 함.
  3. load_human_labels — 채워진 파일에서 trace_id→라벨 로드.
  4. build_distribution — 최종 버킷 분포 + 사람 일치율 산출(결정 게이트 입력).

일치율/분포까지만 산출한다 — L1 계속 vs L2 우선 판단은 사람 몫.
"""

from __future__ import annotations

import csv
import json
import random
from collections import Counter
from pathlib import Path

from scripts.l1_eval.signals import (
    BucketDistribution,
    LabeledQuery,
    LlmBucket,
    RuleBucket,
)

# L1 수요 = 단일 intent 로 고칠 수 있는 실패. L2 수요 = 복합-표현불가.
_L1_RULE = {RuleBucket.ZERO_HIT, RuleBucket.THIN, RuleBucket.SKEW}
_L1_LLM = {LlmBucket.INTENT_MISPICK, LlmBucket.DRIFT}
_L2_LLM = {LlmBucket.COMPOUND_UNEXPRESSIBLE}


def effective_bucket(item: LabeledQuery) -> str:
    """항목의 대표 버킷 문자열 — LLM 이 의미적 실패를 판정했으면 그것을, 아니면 규칙 버킷.

    사람 검증 CSV 에 보여줄 'auto_bucket' 및 일치율 계산 기준.
    """
    if item.llm_bucket is not None and item.llm_bucket is not LlmBucket.NORMAL:
        return item.llm_bucket.value
    return item.rule_bucket.value


def sample_for_review(
    items: list[LabeledQuery], *, n: int = 80, seed: int = 42
) -> list[LabeledQuery]:
    """검증용 샘플을 결정적으로 뽑는다(재현 가능). n 보다 적으면 전부 반환."""
    if len(items) <= n:
        return list(items)
    rng = random.Random(seed)
    idx = sorted(rng.sample(range(len(items)), n))
    return [items[i] for i in idx]


def _review_records(items: list[LabeledQuery]) -> list[dict]:
    return [
        {
            "trace_id": it.signals.trace_id,
            "raw_query": it.signals.raw_query,
            "intent": it.signals.intent or "",
            "auto_bucket": effective_bucket(it),
            "rule_bucket": it.rule_bucket.value,
            "llm_bucket": it.llm_bucket.value if it.llm_bucket else "",
            "llm_rationale": it.llm_rationale or "",
            "human_bucket": "",  # 사람이 채울 열
        }
        for it in items
    ]


def export_human_review(items: list[LabeledQuery], path: Path) -> Path:
    """검증 샘플을 csv 또는 json 으로 내보낸다(확장자로 판별)."""
    path = Path(path)
    records = _review_records(items)
    if path.suffix == ".json":
        path.write_text(
            json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        fieldnames = list(records[0].keys()) if records else ["trace_id", "human_bucket"]
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(records)
    return path


def load_human_labels(path: Path) -> dict[str, str]:
    """사람이 채운 검증 파일에서 trace_id → human_bucket 매핑을 로드한다.

    human_bucket 이 빈 행은 건너뛴다(아직 미검증).
    """
    path = Path(path)
    labels: dict[str, str] = {}
    if path.suffix == ".json":
        for rec in json.loads(path.read_text(encoding="utf-8")):
            hb = (rec.get("human_bucket") or "").strip()
            if hb:
                labels[str(rec["trace_id"])] = hb
    else:
        with open(path, encoding="utf-8") as f:
            for rec in csv.DictReader(f):
                hb = (rec.get("human_bucket") or "").strip()
                if hb:
                    labels[str(rec["trace_id"])] = hb
    return labels


def apply_human_labels(
    items: list[LabeledQuery], labels: dict[str, str]
) -> list[LabeledQuery]:
    """로드한 사람 라벨을 항목에 반영한 새 리스트를 반환(불변 갱신)."""
    out: list[LabeledQuery] = []
    for it in items:
        hb = labels.get(it.signals.trace_id)
        out.append(it.model_copy(update={"human_bucket": hb}) if hb else it)
    return out


def build_distribution(items: list[LabeledQuery]) -> BucketDistribution:
    """최종 버킷 분포 + L1/L2 수요 분해 + (있으면) 사람 일치율을 산출한다.

    L1/L2 수요 분모는 retrieval_total(실제 검색을 시도한 RETRIEVE 트레이스)이다.
    NON_RETRIEVE(action≠RETRIEVE 또는 META)는 rule_counts 로 투명하게 집계하되
    수요 계산에서 제외해 결정 게이트를 정직하게 만든다.
    """
    rule_counts = Counter(it.rule_bucket.value for it in items)
    llm_counts = Counter(
        it.llm_bucket.value for it in items if it.llm_bucket is not None
    )

    # 분모 스코핑: 검색 미시도 턴은 수요 집계에서 뺀다.
    retrieval_items = [it for it in items if it.rule_bucket is not RuleBucket.NON_RETRIEVE]
    non_retrieve = len(items) - len(retrieval_items)

    # turn_kind 세그먼트(RETRIEVE 트레이스만) — DRILL/REFINE 등 = L2 수요 prior.
    turn_kind_counts = Counter(
        it.signals.turn_kind for it in retrieval_items if it.signals.turn_kind
    )

    l1 = sum(1 for it in retrieval_items if _is_l1_demand(it))
    l2 = sum(1 for it in retrieval_items if it.llm_bucket in _L2_LLM)

    reviewed = [it for it in items if it.human_bucket]
    agreement: float | None = None
    if reviewed:
        match = sum(1 for it in reviewed if effective_bucket(it) == it.human_bucket)
        agreement = match / len(reviewed)

    return BucketDistribution(
        total=len(items),
        retrieval_total=len(retrieval_items),
        non_retrieve_total=non_retrieve,
        rule_counts=dict(rule_counts),
        llm_counts=dict(llm_counts),
        turn_kind_counts=dict(turn_kind_counts),
        l1_demand=l1,
        l2_demand=l2,
        human_agreement=agreement,
        human_sample_size=len(reviewed) or None,
    )


def _is_l1_demand(item: LabeledQuery) -> bool:
    # 검색 미시도는 수요 아님(방어 — 호출부가 이미 제외하지만 이중 안전).
    if item.rule_bucket is RuleBucket.NON_RETRIEVE:
        return False
    if item.llm_bucket in _L1_LLM:
        return True
    # LLM 이 복합-표현불가로 판정하지 않았고 규칙이 단일-intent 실패면 L1 수요.
    if item.llm_bucket in _L2_LLM:
        return False
    return item.rule_bucket in _L1_RULE


def format_report(dist: BucketDistribution) -> str:
    """분포를 사람이 읽는 리포트 문자열로 포맷(stdout 출력용)."""
    lines = [
        "=== L1 Phase 0 실패 버킷 분포 ===",
        f"총 질의: {dist.total}  "
        f"(검색 시도 RETRIEVE: {dist.retrieval_total}, "
        f"검색 미시도 NON_RETRIEVE: {dist.non_retrieve_total})",
        "",
        "[규칙 자동 라벨]  (%는 RETRIEVE 분모 기준, NON_RETRIEVE 은 전체 기준)",
    ]
    for k, v in sorted(dist.rule_counts.items(), key=lambda kv: -kv[1]):
        # NON_RETRIEVE 는 분모에서 빠지므로 전체 기준 %, 나머지는 RETRIEVE 분모 기준 %.
        base = dist.total if k == "NON_RETRIEVE" else dist.retrieval_total
        pct = 100 * v / base if base else 0
        lines.append(f"  {k:24s} {v:5d}  ({pct:5.1f}%)")
    lines.append("")
    lines.append("[LLM 판단 라벨]  (%는 RETRIEVE 분모 기준)")
    for k, v in sorted(dist.llm_counts.items(), key=lambda kv: -kv[1]):
        pct = 100 * v / dist.retrieval_total if dist.retrieval_total else 0
        lines.append(f"  {k:24s} {v:5d}  ({pct:5.1f}%)")
    lines.append("")
    lines.append("[turn_kind 세그먼트]  (RETRIEVE 트레이스, DRILL/REFINE = L2 수요 prior)")
    if dist.turn_kind_counts:
        for k, v in sorted(dist.turn_kind_counts.items(), key=lambda kv: -kv[1]):
            pct = 100 * v / dist.retrieval_total if dist.retrieval_total else 0
            lines.append(f"  {k:24s} {v:5d}  ({pct:5.1f}%)")
    else:
        lines.append("  (turn_kind 신호 없음 — 구 트레이스이거나 미배포)")
    lines.append("")
    lines.append("[결정 게이트 입력]  (분모 = RETRIEVE 트레이스)")
    lines.append(f"  L1 수요(단일-intent 실패):     {dist.l1_demand}")
    lines.append(f"  L2 수요(복합-표현불가):        {dist.l2_demand}")
    if dist.human_agreement is not None:
        lines.append("")
        lines.append(
            f"[사람 검증] 표본 {dist.human_sample_size}건, "
            f"자동↔사람 일치율 {dist.human_agreement:.1%}"
        )
    lines.append("")
    lines.append("※ 판단(L1 계속 vs L2 우선)은 사람 몫 — 이 리포트는 수치만 제공한다.")
    return "\n".join(lines)
