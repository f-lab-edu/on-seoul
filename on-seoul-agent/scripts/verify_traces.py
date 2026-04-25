"""chat_agent_traces 저장 데이터 검증 스크립트.

on_ai.chat_agent_traces 테이블에서 최근 N건을 조회하고,
각 row의 trace JSONB 필드가 필수 키를 포함하는지 검증한다.

필수 키:
  - intent     : str, SQL_SEARCH / VECTOR_SEARCH / MAP / FALLBACK 중 하나
  - nodes      : list, 실행 노드 경로
  - elapsed_sec: float, 소요 시간

사용법:
  uv run python -m scripts.verify_traces [--limit N]

검증 결과는 stdout에 출력된다.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_VALID_INTENTS = frozenset({"SQL_SEARCH", "VECTOR_SEARCH", "MAP", "FALLBACK"})
_REQUIRED_KEYS = ("intent", "nodes", "elapsed_sec")


def verify_trace_row(row: dict[str, Any]) -> dict[str, Any]:
    """단일 trace row의 필수 키 존재 여부와 타입을 검증한다.

    Args:
        row: {"message_id": int, "trace": dict} 형태의 row.

    Returns:
        {"message_id": int, "status": "PASS"|"FAIL", "missing_keys": list[str]}
    """
    message_id: int = row["message_id"]
    trace: dict[str, Any] = row.get("trace") or {}
    missing: list[str] = []

    # intent 검증: 존재하며 유효한 값이어야 한다
    intent = trace.get("intent")
    if intent not in _VALID_INTENTS:
        missing.append("intent")

    # nodes 검증: 존재하며 list이어야 한다
    nodes = trace.get("nodes")
    if not isinstance(nodes, list):
        missing.append("nodes")

    # elapsed_sec 검증: 존재하며 숫자(int/float)이어야 한다
    elapsed_sec = trace.get("elapsed_sec")
    if not isinstance(elapsed_sec, (int, float)):
        missing.append("elapsed_sec")

    status = "PASS" if not missing else "FAIL"
    return {"message_id": message_id, "status": status, "missing_keys": missing}


async def fetch_and_verify(
    session: AsyncSession,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """DB에서 최근 N건을 조회하고 각 row를 검증한다.

    Args:
        session: on_ai DB AsyncSession.
        limit: 조회할 최대 건수 (기본값 10).

    Returns:
        각 row의 검증 결과 목록.
    """
    result = await session.execute(
        text(
            "SELECT message_id, trace "
            "FROM chat_agent_traces "
            "ORDER BY created_at DESC "
            "LIMIT :limit"
        ),
        {"limit": limit},
    )
    rows = result.mappings().all()
    return [verify_trace_row(dict(row)) for row in rows]


def _print_results(results: list[dict[str, Any]]) -> int:
    """검증 결과를 stdout에 출력하고 FAIL 건수를 반환한다."""
    total = len(results)
    fail_count = 0

    if not results:
        print("조회된 trace가 없습니다.")
        return 0

    for r in results:
        status = r["status"]
        message_id = r["message_id"]
        if status == "PASS":
            print(f"[PASS] message_id={message_id}")
        else:
            fail_count += 1
            missing = ", ".join(r["missing_keys"])
            print(f"[FAIL] message_id={message_id} — 누락/오류 키: {missing}")

    print(f"\n합계: {total}건 검사 | PASS: {total - fail_count} | FAIL: {fail_count}")
    return fail_count


async def _main(limit: int) -> int:
    """메인 실행 함수. 반환값: FAIL 건수 (0이면 exit 0)."""
    from core.database import ai_session_ctx

    async with ai_session_ctx() as session:
        results = await fetch_and_verify(session, limit=limit)

    return _print_results(results)


def main() -> None:
    parser = argparse.ArgumentParser(description="chat_agent_traces 검증 스크립트")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="검증할 최근 trace 건수 (기본값: 10)",
    )
    args = parser.parse_args()

    fail_count = asyncio.run(_main(args.limit))
    sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    main()
