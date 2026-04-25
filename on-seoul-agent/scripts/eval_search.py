"""벡터 검색 품질 정성 평가 스크립트.

사용법
------
# 내장 샘플 쿼리셋 20건 실행 (기본 top-5)
uv run python scripts/eval_search.py

# 단일 질의 실행
uv run python scripts/eval_search.py --query "강남구 수영장"

# top-k 조정
uv run python scripts/eval_search.py --top-k 10
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (scripts/ 에서 실행 시 패키지 임포트를 위해)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

try:
    from core.config import settings
except Exception as exc:
    sys.exit(
        f"[eval_search] 환경변수 설정 오류: {exc}\n"
        ".env 파일 또는 ON_AI_DATABASE_URL / GOOGLE_API_KEY 환경변수를 확인하세요."
    )

from llm.client import get_embeddings
from tools.vector_search import vector_search

# ---------------------------------------------------------------------------
# 내장 샘플 쿼리셋 (20건) — 카테고리·지역·키워드·의미 다양하게 구성
# ---------------------------------------------------------------------------
_SAMPLE_QUERIES: list[str] = [
    "아이랑 체험할 수 있는 시설",
    "강남구 수영장",
    "마포구 문화행사",
    "무료로 이용할 수 있는 체육 시설",
    "어린이 교육 프로그램",
    "노인 복지 서비스",
    "강서구 문화센터",
    "등산 관련 체험 행사",
    "서울 시내 공연 관람",
    "장애인 이용 가능한 스포츠 시설",
    "청소년 대상 진로 교육",
    "주말에 이용 가능한 수영 강습",
    "송파구 체육관",
    "가족이 함께 즐길 수 있는 야외 활동",
    "직장인 대상 야간 강좌",
    "성동구 공연 및 전시",
    "영유아 발달 지원 프로그램",
    "종로구 역사 문화 투어",
    "중구 생활체육 강습",
    "온라인 참여 가능한 교육 프로그램",
]


def _print_header(query: str) -> None:
    line = "=" * 72
    print(line)
    print(f"질의: {query}")
    print(line)


def _print_results(results: list[dict]) -> None:
    if not results:
        print("  (결과 없음)")
        return
    col_name = "시설명"
    col_area = "지역"
    col_sim = "유사도"
    print(
        f"  {'순위':<4} {col_name:<30} {col_area:<12} {col_sim}"
    )
    print(f"  {'-'*4} {'-'*30} {'-'*12} {'-'*6}")
    for i, row in enumerate(results, start=1):
        name = (row.get("service_name") or "")[:28]
        metadata = row.get("metadata") or {}
        area = (metadata.get("area_name") or "")[:10]
        similarity = row.get("similarity")
        sim_str = f"{similarity:.4f}" if similarity is not None else "N/A"
        print(f"  {i:<4} {name:<30} {area:<12} {sim_str}")
    print()


async def _run_query(
    query: str,
    top_k: int,
    embeddings: Embeddings,
    session: AsyncSession,
) -> None:
    _print_header(query)
    query_vector = await embeddings.aembed_query(query)
    results = await vector_search(session, query_vector, top_k=top_k)
    _print_results(results)


async def run(query: str | None, top_k: int) -> None:
    on_ai_engine = create_async_engine(
        settings.on_ai_database_url,
        echo=False,
        connect_args={"statement_cache_size": 0},
    )
    OnAiSession = async_sessionmaker(on_ai_engine, expire_on_commit=False)
    embeddings = get_embeddings()

    queries = [query] if query else _SAMPLE_QUERIES

    async with OnAiSession() as session:
        for q in queries:
            await _run_query(q, top_k, embeddings, session)

    await on_ai_engine.dispose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="벡터 검색 품질 정성 평가")
    parser.add_argument(
        "--query",
        type=str,
        default=None,
        help="단일 질의 실행 (미입력 시 내장 샘플 쿼리셋 20건 실행)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        dest="top_k",
        help="반환할 최대 결과 수 (기본값: 5)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(run(args.query, args.top_k))
