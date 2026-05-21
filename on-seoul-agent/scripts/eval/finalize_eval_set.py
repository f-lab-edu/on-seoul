"""candidates_review.tsv → eval_set_holdout.tsv 변환기.

generate_candidates.py --batch 로 생성한 candidates_review.tsv 의 is_correct 컬럼에
'y' 를 채운 뒤 이 스크립트를 실행한다.

입력 형식 (candidates_review.tsv):
    query  intent  sub_intent  refined_query  rank  service_id  service_name
    area_name  max_class_name  service_status  channels  score  is_correct

출력 형식 (eval_set_holdout.tsv):
    query  intent  sub_intent  correct_service_ids

    correct_service_ids: is_correct='y' 인 service_id 를 rank 순으로 쉼표 연결

사용법
------
  uv run python scripts/eval/finalize_eval_set.py \\
      --input  scripts/eval/candidates_review.tsv \\
      --output scripts/eval/eval_set_holdout.tsv

  # 기존 holdout 에 append
  uv run python scripts/eval/finalize_eval_set.py \\
      --input  scripts/eval/candidates_review_new.tsv \\
      --output scripts/eval/eval_set_holdout.tsv \\
      --append
"""

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load_reviewed(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def convert(rows: list[dict]) -> list[dict]:
    """is_correct='y' 행을 query 단위로 집계한다."""
    # (query, intent, sub_intent) → {rank: service_id}
    correct: dict[tuple, dict[int, str]] = defaultdict(dict)
    meta: dict[tuple, tuple] = {}  # key → (intent, sub_intent)

    for row in rows:
        key = (row["query"], row["intent"], row["sub_intent"])
        meta[key] = (row["intent"], row["sub_intent"])
        if row.get("is_correct", "").strip().lower() == "y":
            try:
                rank = int(row.get("rank", 0))
            except ValueError:
                rank = 999
            correct[key][rank] = row["service_id"]

    results = []
    # 정답이 1건 이상인 질의만 포함
    for key, ranked in correct.items():
        query, intent, sub_intent = key
        service_ids = [sid for _, sid in sorted(ranked.items())]
        results.append({
            "query":               query,
            "intent":              intent,
            "sub_intent":          sub_intent,
            "correct_service_ids": ",".join(service_ids),
        })

    return results


def write_holdout(records: list[dict], path: Path, append: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["query", "intent", "sub_intent", "correct_service_ids"]
    mode = "a" if append and path.exists() else "w"
    with path.open(mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        if mode == "w":
            writer.writeheader()
        writer.writerows(records)


def print_summary(records: list[dict]) -> None:
    intent_counts = Counter(r["intent"] for r in records)
    sub_intent_counts = Counter(
        r["sub_intent"] for r in records if r["sub_intent"]
    )
    print(f"\n총 {len(records)}건")
    print("Intent 분포:")
    for intent, cnt in sorted(intent_counts.items()):
        print(f"  {intent:<20} {cnt}건")
    if sub_intent_counts:
        print("SubIntent 분포 (VECTOR_SEARCH):")
        for si, cnt in sorted(sub_intent_counts.items()):
            print(f"  {si:<20} {cnt}건")

    # 정답 0건 경고
    zero = [r for r in records if not r["correct_service_ids"]]
    if zero:
        print(f"\n[경고] 정답 service_id 없는 질의 {len(zero)}건:")
        for r in zero:
            print(f"  - {r['query']}")


def main(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"오류: 파일 없음 — {input_path}", file=sys.stderr)
        sys.exit(1)

    rows = load_reviewed(input_path)
    if not rows:
        print("입력 파일이 비어 있습니다.", file=sys.stderr)
        sys.exit(1)

    total_rows = len(rows)
    labeled = sum(1 for r in rows if r.get("is_correct", "").strip().lower() == "y")
    empty = sum(1 for r in rows if not r.get("is_correct", "").strip())

    print(f"입력: {total_rows}행 (정답 표시 {labeled}건, 미표시 {empty}건)")

    if empty > 0 and not args.force:
        print(
            f"\n[경고] is_correct 가 비어 있는 행이 {empty}건 있습니다.\n"
            "계속하려면 --force 옵션을 추가하세요."
        )
        sys.exit(1)

    records = convert(rows)
    write_holdout(records, output_path, append=args.append)
    print_summary(records)
    print(f"\n{'추가' if args.append else '저장'}됨: {output_path}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="후보 검토 파일 → eval_set_holdout.tsv")
    parser.add_argument("--input", required=True, help="candidates_review.tsv 경로")
    parser.add_argument("--output", required=True, help="eval_set_holdout.tsv 경로")
    parser.add_argument("--append", action="store_true",
                        help="기존 holdout 파일에 추가 (기본값: 덮어씀)")
    parser.add_argument("--force", action="store_true",
                        help="is_correct 미표시 행이 있어도 계속 진행")
    return parser.parse_args()


if __name__ == "__main__":
    main(_parse_args())
