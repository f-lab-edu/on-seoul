"""L1 Phase 6 CLI — critic-on 트레이스에서 critic 동작 지표 산출.

배포 후(enable_retrieval_critic=true) N일 실 트래픽을 분석해 세 질문에 답한다:
  · critic 이 실제로 복구를 늘리나? (REPLAN 복구율)
  · 지연 얼마나?                    (critic 스팬 소요 + 진입/미진입 총 지연 비교)
  · 80% 경로 보존되나?              (RETRIEVE 중 critic 미진입 비율)

end-to-end 흐름:
  1. 트레이스 로드: 라이브(Langfuse) 또는 드라이런(번들/지정 픽스처).
  2. CriticTrace 로 구조화(root 최종 신호 + retrieval_critic 자식 스팬 라운드들).
  3. 지표 산출(compute_metrics) → 사람이 읽는 리포트(format_report) stdout.

사용법 (드라이런 — 자격증명 불필요, 파이프라인 증명):
  uv run python -m scripts.eval.l1.run_critic_metrics --dry-run
  uv run python -m scripts.eval.l1.run_critic_metrics --dry-run --fixture path/to/traces.json

사용법 (라이브 — 사람이 플래그 켜고 N일 후 자격증명 주입해 실행):
  # .env 에 LANGFUSE_ENABLED=true / PUBLIC_KEY / SECRET_KEY / HOST 설정 후:
  uv run python -m scripts.eval.l1.run_critic_metrics --days 7 --limit 500

라이브 조회는 read-only 다(Langfuse trace.list/get). on_data 쓰기 없음.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.eval.l1.critic_metrics import (
    compute_metrics,
    fetch_live_critic_traces,
    format_report,
    load_fixture_critic_traces,
)


def main() -> None:
    p = argparse.ArgumentParser(description="L1 Phase 6 critic 동작 지표 측정")
    p.add_argument("--dry-run", action="store_true", help="라이브 대신 픽스처로 실행")
    p.add_argument("--fixture", help="드라이런 시 사용할 JSON 픽스처 경로(기본: 번들 critic_traces)")
    p.add_argument("--days", type=int, default=7, help="라이브 조회 기간(일)")
    p.add_argument("--limit", type=int, default=500, help="라이브 최대 트레이스 수")
    args = p.parse_args()

    if args.dry_run:
        fixture = Path(args.fixture) if args.fixture else None
        traces = load_fixture_critic_traces(fixture)
        print(f"[드라이런] critic 픽스처 {len(traces)}건 로드")
    else:
        traces = fetch_live_critic_traces(days=args.days, limit=args.limit)
        print(f"[라이브] 최근 {args.days}일 트레이스 {len(traces)}건 로드")

    print()
    print(format_report(compute_metrics(traces)))


if __name__ == "__main__":
    main()
