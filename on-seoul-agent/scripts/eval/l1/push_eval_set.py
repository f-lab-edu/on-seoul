"""평가셋 ②(회귀/품질 고정 데이터셋) Langfuse 등록 CLI.

CURATED_CASES 를 Langfuse Dataset 으로 등록한다. Phase 6 baseline↔after 채점에 재사용.

사용법 (드라이런 — 자격증명 불필요, 등록 페이로드만 출력):
  uv run python -m scripts.eval.l1.push_eval_set --dry-run

사용법 (라이브 — 사람이 자격증명 주입 후 실행):
  # .env 에 LANGFUSE_PUBLIC_KEY / SECRET_KEY / (HOST) 설정 후:
  uv run python -m scripts.eval.l1.push_eval_set --dataset l1-retrieval-critic
"""

from __future__ import annotations

import argparse
import json

from scripts.eval.l1.eval_set import CURATED_CASES, push_dataset


def _build_client():
    """settings 자격증명으로 Langfuse 클라이언트를 만든다(라이브 전용)."""
    from langfuse import Langfuse

    from core.config import settings

    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        raise RuntimeError(
            "Langfuse 키 미설정 — LANGFUSE_PUBLIC_KEY/SECRET_KEY 를 .env 로 주입한 뒤 "
            "실행하세요. 자격증명 없이 검증하려면 --dry-run 을 쓰세요."
        )
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )


def main() -> None:
    p = argparse.ArgumentParser(description="L1 평가셋 ② Langfuse Dataset 등록")
    p.add_argument(
        "--dataset",
        default="l1-retrieval-critic-eval",
        help="Langfuse Dataset 이름",
    )
    p.add_argument("--dry-run", action="store_true", help="등록 없이 페이로드만 출력")
    args = p.parse_args()

    client = None if args.dry_run else _build_client()
    items = push_dataset(
        CURATED_CASES,
        client=client,
        dataset_name=args.dataset,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print(f"[드라이런] 등록될 아이템 {len(items)}건 (dataset={args.dataset}):")
        print(json.dumps(items, ensure_ascii=False, indent=2))
    else:
        client.flush()
        print(f"[라이브] {len(items)}건을 Dataset '{args.dataset}' 에 등록했습니다.")


if __name__ == "__main__":
    main()
