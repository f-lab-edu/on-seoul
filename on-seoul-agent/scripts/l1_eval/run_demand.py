"""평가셋 ①(수요 측정) 파이프라인 CLI — 트레이스→라벨→분류→검증→분포.

end-to-end 흐름:
  1. 트레이스 로드: 라이브(Langfuse) 또는 드라이런(번들/지정 픽스처).
  2. 규칙 자동 라벨(공짜).
  3. LLM 판단 라벨(--classify; NORMAL 규칙 항목만 대상 — 규칙이 이미 실패로 잡은 건 제외).
  4. 사람 검증 샘플 내보내기(--export) 및/또는 채워진 라벨 로드(--human).
  5. 버킷 분포 리포트 stdout 출력(결정 게이트 입력).

사용법 (드라이런 — 자격증명 불필요, 파이프라인 증명):
  uv run python -m scripts.l1_eval.run_demand --dry-run
  uv run python -m scripts.l1_eval.run_demand --dry-run --export review.csv
  uv run python -m scripts.l1_eval.run_demand --dry-run --human review.csv

사용법 (라이브 — 사람이 자격증명 주입 후 실행):
  # .env 에 LANGFUSE_ENABLED=true / PUBLIC_KEY / SECRET_KEY / HOST 설정 후:
  uv run python -m scripts.l1_eval.run_demand --days 14 --limit 500 --classify \
      --export review.csv

  # 사람이 review.csv 의 human_bucket 열을 채운 뒤 최종 분포:
  uv run python -m scripts.l1_eval.run_demand --days 14 --classify --human review.csv

--classify 는 실제 LLM 을 호출한다(비용 발생). 미지정 시 규칙 라벨만으로 분포를 낸다.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from scripts.l1_eval.aggregate import (
    apply_human_labels,
    build_distribution,
    export_human_review,
    format_report,
    load_human_labels,
    sample_for_review,
)
from scripts.l1_eval.extract import fetch_live_traces, load_fixture_traces
from scripts.l1_eval.llm_classifier import FailureClassifier
from scripts.l1_eval.rule_labeler import label_rule
from scripts.l1_eval.signals import (
    LabeledQuery,
    LlmBucket,
    QuerySignals,
    RuleBucket,
)


def _rule_label(signals: list[QuerySignals]) -> list[LabeledQuery]:
    return [LabeledQuery(signals=s, rule_bucket=label_rule(s)) for s in signals]


async def _llm_label(items: list[LabeledQuery]) -> list[LabeledQuery]:
    """규칙상 NORMAL 인 항목만 LLM 분류(규칙이 이미 실패로 잡은 건 재분류 낭비 방지)."""
    clf = FailureClassifier()
    out: list[LabeledQuery] = []
    for it in items:
        if it.rule_bucket is not RuleBucket.NORMAL:
            out.append(it)
            continue
        res = await clf.classify(it.signals)
        out.append(
            it.model_copy(
                update={"llm_bucket": res.bucket, "llm_rationale": res.rationale}
            )
        )
    return out


async def _amain(args: argparse.Namespace) -> None:
    # 1. 로드
    if args.dry_run:
        fixture = Path(args.fixture) if args.fixture else None
        signals = load_fixture_traces(fixture)
        print(f"[드라이런] 픽스처 {len(signals)}건 로드")
    else:
        signals = fetch_live_traces(days=args.days, limit=args.limit)
        print(f"[라이브] 최근 {args.days}일 트레이스 {len(signals)}건 로드")

    # 2. 규칙 라벨
    items = _rule_label(signals)

    # 3. LLM 판단 라벨(선택)
    if args.classify:
        # 드라이런에서 실제 LLM 호출을 원치 않으면 마킹만(NORMAL) 하고 스킵.
        if args.dry_run and not args.classify_live:
            items = [
                it.model_copy(
                    update={
                        "llm_bucket": LlmBucket.NORMAL,
                        "llm_rationale": "드라이런 — LLM 미호출(--classify-live 로 강제)",
                    }
                )
                if it.rule_bucket is RuleBucket.NORMAL
                else it
                for it in items
            ]
        else:
            items = await _llm_label(items)

    # 4. 사람 검증
    if args.human:
        labels = load_human_labels(Path(args.human))
        items = apply_human_labels(items, labels)
        print(f"[검증] 사람 라벨 {len(labels)}건 반영")

    if args.export:
        sample = sample_for_review(items, n=args.sample_size, seed=args.seed)
        path = export_human_review(sample, Path(args.export))
        print(f"[검증] 샘플 {len(sample)}건 → {path} (human_bucket 열을 채운 뒤 --human 로 재실행)")

    # 5. 분포
    dist = build_distribution(items)
    print()
    print(format_report(dist))


def main() -> None:
    p = argparse.ArgumentParser(description="L1 Phase 0 수요 측정 파이프라인")
    p.add_argument("--dry-run", action="store_true", help="라이브 대신 픽스처로 실행")
    p.add_argument("--fixture", help="드라이런 시 사용할 JSON 픽스처 경로(기본: 번들)")
    p.add_argument("--days", type=int, default=14, help="라이브 조회 기간(일)")
    p.add_argument("--limit", type=int, default=500, help="라이브 최대 트레이스 수")
    p.add_argument("--classify", action="store_true", help="LLM 판단 라벨 수행")
    p.add_argument(
        "--classify-live",
        action="store_true",
        help="드라이런에서도 실제 LLM 호출(비용 발생)",
    )
    p.add_argument("--export", help="사람 검증 샘플 내보내기 경로(.csv/.json)")
    p.add_argument("--human", help="사람이 채운 검증 파일 경로(.csv/.json)")
    p.add_argument("--sample-size", type=int, default=80, help="검증 샘플 건수(50~100 권장)")
    p.add_argument("--seed", type=int, default=42, help="샘플링 시드(재현용)")
    args = p.parse_args()

    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
