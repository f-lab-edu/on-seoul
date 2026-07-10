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

from scripts.eval.l1.signals import (
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
            "action": it.signals.action or "",
            "turn_kind": it.signals.turn_kind or "",
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
        "=== L1 실패 버킷 분포 ===",
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


# ── 사람 검증 확장 택소노미 → 수요 범주 ──────────────────────────────────
# 자동 택소노미(LlmBucket/RuleBucket)로는 못 담는 세분 실패를 사람이 붙일 수 있다
# (intent/sub_intent/action/turn_kind 오선택 등). 각 라벨을 결정 게이트 수요로 매핑한다.
_HUMAN_DEMAND: dict[str, str] = {
    # L1 — retrieval-critic 이 겨냥하는 검색 품질 실패
    "INTENT_MISPICK": "L1",
    "SUB_INTENT_MISPICK": "L1",
    "DRIFT": "L1",
    "ZERO_HIT": "L1",
    "THIN": "L1",
    "SKEW": "L1",
    # L2 — 복합 제약(단일 intent 로 표현 불가)
    "COMPOUND_UNEXPRESSIBLE": "L2",
    # 상류(triage/intake) — L1/L2 로 안 풀림. TriageAgent action / intake turn_kind 오분류.
    "ACTION_MISPICK": "UPSTREAM",
    "TURN_KIND_MISPICK": "UPSTREAM",
    # 정상 / 검색 미시도
    "NORMAL": "NORMAL",
    "NON_RETRIEVE": "EXCLUDED",
}
_DEMAND_ORDER = ["L1", "L2", "UPSTREAM", "NORMAL", "EXCLUDED", "UNKNOWN"]
_DEMAND_LABEL = {
    "L1": "L1 수요(검색 품질 — retrieval-critic)",
    "L2": "L2 수요(복합-표현불가 — planner)",
    "UPSTREAM": "상류 수요(triage/intake 오분류)",
    "NORMAL": "정상",
    "EXCLUDED": "제외(검색 미시도)",
    "UNKNOWN": "미분류(알 수 없는 라벨)",
}


def human_demand_class(bucket: str) -> str:
    """사람 검증 라벨 → 수요 범주(L1/L2/UPSTREAM/NORMAL/EXCLUDED/UNKNOWN)."""
    return _HUMAN_DEMAND.get((bucket or "").strip(), "UNKNOWN")


def report_labeled_csv(path: Path) -> str:
    """사람이 human_bucket 을 채운 라벨 CSV 로 최종 분포를 오프라인 산출한다.

    라이브 재조회·LLM 재분류 없이 CSV 만으로 산출(재현 가능·무비용). human_bucket 이
    빈 행은 미검증으로 두고 수요 분해에서 제외한다. auto_bucket / llm_bucket 컬럼이 있으면
    자동↔사람 정확도(특히 LLM 의 L2 과다판정)를 함께 보고한다.
    """
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    labeled = [r for r in rows if (r.get("human_bucket") or "").strip()]
    base = len(labeled)
    human_counts = Counter(r["human_bucket"].strip() for r in labeled)
    demand = Counter(human_demand_class(r["human_bucket"]) for r in labeled)

    lines = [
        "=== L1 최종 분포 (사람 검증 ground truth) ===",
        f"파일: {path}",
        f"총 {len(rows)}행, 검증됨 {base}행 (human_bucket 채움)",
        "",
        "[사람 라벨 분포]  (%는 검증행 기준)",
    ]
    for k, v in human_counts.most_common():
        pct = 100 * v / base if base else 0
        lines.append(f"  {k:24s} {v:5d}  ({pct:5.1f}%)")
    lines.append("")
    lines.append("[수요 분해 (사람 ground truth)]")
    for k in _DEMAND_ORDER:
        if demand.get(k):
            lines.append(f"  {_DEMAND_LABEL[k]:34s} {demand[k]:5d}")

    if labeled and "auto_bucket" in labeled[0]:
        match = sum(
            1
            for r in labeled
            if (r.get("auto_bucket") or "").strip() == r["human_bucket"].strip()
        )
        lines.append("")
        lines.append("[자동 라벨 정확도]  (auto_bucket ↔ human_bucket)")
        pct = 100 * match / base if base else 0
        lines.append(f"  전체 일치율: {pct:.1f}%  ({match}/{base})")
        if "llm_bucket" in labeled[0]:
            l2_called = [
                r
                for r in labeled
                if (r.get("llm_bucket") or "").strip() == "COMPOUND_UNEXPRESSIBLE"
            ]
            if l2_called:
                over = sum(
                    1 for r in l2_called if human_demand_class(r["human_bucket"]) != "L2"
                )
                op = 100 * over / len(l2_called)
                lines.append(
                    f"  LLM COMPOUND(L2) 판정 {len(l2_called)}건 중 "
                    f"사람이 L2 아님: {over}건 (과다판정 {op:.0f}%)"
                )
    lines.append("")
    lines.append("※ 수요 우선순위(L1/상류/L2) 판단은 사람 몫 — 리포트는 수치만 제공한다.")
    return "\n".join(lines)
