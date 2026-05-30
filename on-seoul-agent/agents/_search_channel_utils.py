"""검색 채널 공통 유틸리티.

각 에이전트/노드가 검색 결과 rows 를 ChannelHit 리스트로 변환할 때 사용하는
_to_hits 헬퍼를 제공한다. 노드/에이전트 코드에서 중복 rank 계산 로직을 제거한다.
"""

from collections.abc import Callable
from typing import Any

from schemas.search import ChannelHit


def _to_hits(
    rows: list[dict[str, Any]],
    *,
    score_field: str | None,
    meta_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> list[ChannelHit]:
    """검색 결과 rows 를 ChannelHit 리스트로 변환한다.

    Args:
        rows: 검색 도구가 반환한 딕셔너리 리스트.
              각 row 에는 반드시 ``service_id`` 키가 있어야 한다.
        score_field: 점수 키 이름 (예: "similarity", "bm25_score", "rrf_score").
                     None 이거나 row 에 해당 키가 없으면 score=None.
        meta_fn: row 에서 ``meta`` dict 를 생성하는 함수.
                 None 이면 빈 dict 가 사용된다.

    Returns:
        1-based rank 가 할당된 ChannelHit 리스트.
        입력 rows 의 순서가 rank 순서가 된다.
    """
    hits: list[ChannelHit] = []
    for i, row in enumerate(rows, start=1):
        score: float | None = None
        if score_field and score_field in row:
            raw = row[score_field]
            score = float(raw) if raw is not None else None
        meta = meta_fn(row) if meta_fn else {}
        hits.append(
            ChannelHit(
                rank=i,
                service_id=row["service_id"],
                score=score,
                meta=meta,
            )
        )
    return hits
