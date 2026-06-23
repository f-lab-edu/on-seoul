"""on_data 읽기 게이트웨이 — data_session_ctx 세션 수명 캡슐화(B2-1).

자원=on_data 읽기(public_service_reservations, on_data_reader). 게이트웨이는
"세션 수명·자원 선택"만 책임지고 tool 은 "쿼리 내용·SQL 안전성"을 책임진다(보완 관계).

노드 로컬 세션(제약 #2/#5): 각 메서드는 호출당 1회 acquire-use-release.
data_session_ctx() 로 풀에서 세션을 잡고 사용 후 즉시 반납한다(장수명 세션 노출 금지).

테스트 patch 타깃:
  - 세션: agents._ondata_gateway.data_session_ctx
  - tool: agents._ondata_gateway._hydrate_services / agents._ondata_gateway._map_search
"""

from contextlib import asynccontextmanager
from typing import Any

from core.database import data_session_ctx
from tools.hydrate_services import hydrate_services as _hydrate_services
from tools.map_search import map_search as _map_search


@asynccontextmanager
async def session():
    """sql/analytics 에이전트에 넘길 on_data 세션 1회 (acquire-use-release).

    호출자가 with 블록 안에서만 세션을 쓰고 블록 종료 시 즉시 반납한다.
    """
    async with data_session_ctx() as s:
        yield s


async def hydrate(ids: list[str]) -> list[dict[str, Any]]:
    """service_id 목록을 최신 원본으로 hydrate (호출당 1회 acquire-use-release)."""
    async with data_session_ctx() as s:
        return await _hydrate_services(s, ids)


async def map_proximity(lat: float, lng: float, radius_m: int) -> dict[str, Any]:
    """좌표 근접 검색 — raw GeoJSON 반환까지만 (호출당 1회 acquire-use-release).

    features→ChannelData 조립은 호출 노드(map_node) 책임으로 둔다(게이트웨이는 raw 반환).
    """
    async with data_session_ctx() as s:
        return await _map_search(s, lat, lng, radius_m=radius_m)


__all__ = ["session", "hydrate", "map_proximity"]
