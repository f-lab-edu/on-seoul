"""on_data 읽기 게이트웨이 — data_session_ctx 세션 수명 캡슐화(B2-1/B3-1).

자원=on_data 읽기(public_service_reservations, on_data_reader). 게이트웨이는
"세션 수명·자원 선택"만 책임지고 tool 은 "쿼리 내용·SQL 안전성"을 책임진다(보완 관계).

노드 로컬 세션(제약 #2/#5): 각 메서드는 호출당 1회 acquire-use-release.
data_session_ctx() 로 풀에서 세션을 잡고 사용 후 즉시 반납한다(장수명 세션 노출 금지).

B3-1(선택적 주입): on_data 읽기를 `OnDataReader` 클래스로 승격해 RetrievalNodes 가
생성자 주입으로 받을 수 있게 했다. 모듈 함수(session/hydrate/map_proximity)는
기본 인스턴스(default_reader)로 위임해 다른 페이즈(Reference 등)와의 모듈 경유
호출부 호환을 유지한다. 따라서 Retrieval 만 농도를 올리고 나머지는 B2 그대로다.

테스트 patch 타깃(모듈 경유 호출부용 — Reference 등):
  - 세션: agents._ondata_gateway.data_session_ctx
  - tool: agents._ondata_gateway._hydrate_services / agents._ondata_gateway._map_search
Retrieval 단위 테스트는 patch 대신 가짜 OnDataReader 를 RetrievalNodes 에 주입한다.
"""

from contextlib import asynccontextmanager
from typing import Any

from core.database import data_session_ctx
from tools.hydrate_services import hydrate_services as _hydrate_services
from tools.map_search import map_search as _map_search


class OnDataReader:
    """on_data 읽기 자원 게이트웨이 — 세션 수명·자원 선택 책임(B3-1 주입 가능).

    각 메서드는 호출당 1회 acquire-use-release(제약 #2): data_session_ctx() 로
    풀에서 세션을 잡고 tool 호출 후 즉시 반납한다(장수명 세션 노출 금지).
    무상태이므로 프로세스 내 단일 default_reader 공유가 안전하다.
    """

    @asynccontextmanager
    async def session(self):
        """sql/analytics 에이전트에 넘길 on_data 세션 1회 (acquire-use-release).

        호출자가 with 블록 안에서만 세션을 쓰고 블록 종료 시 즉시 반납한다.
        """
        async with data_session_ctx() as s:
            yield s

    async def hydrate(self, ids: list[str]) -> list[dict[str, Any]]:
        """service_id 목록을 최신 원본으로 hydrate (호출당 1회 acquire-use-release)."""
        async with data_session_ctx() as s:
            return await _hydrate_services(s, ids)

    async def map_proximity(
        self, lat: float, lng: float, radius_m: int
    ) -> dict[str, Any]:
        """좌표 근접 검색 — raw GeoJSON 반환까지만 (호출당 1회 acquire-use-release).

        features→ChannelData 조립은 호출 노드(map_node) 책임으로 둔다(게이트웨이는 raw 반환).
        """
        async with data_session_ctx() as s:
            return await _map_search(s, lat, lng, radius_m=radius_m)


#: 프로세스 공유 기본 reader. 모듈 함수와 RetrievalNodes 기본 의존이 이를 공유한다.
default_reader = OnDataReader()


# B2 호환 모듈 함수 — default_reader 위임. 현재 살아있는 호출자는 hydrate() 뿐이다
# (reference.py:rehydrate_node). session()/map_proximity() 는 Retrieval 이 주입 경로로
# 옮겨가며 호출자가 사라졌으나, 아직 B2(모듈 경유)인 다른 페이즈가 on_data 세션·근접
# 검색을 쓰게 될 때를 대비해 예약 표면으로 남긴다(가역성, 설계 기준 ⑦).
# 정리 트리거: 모든 페이즈가 게이트웨이 주입으로 전환되면 이 두 함수를 제거한다.
@asynccontextmanager
async def session():
    """B2 호환 모듈 함수 — default_reader.session() 위임(현재 호출자 없음, 예약)."""
    async with default_reader.session() as s:
        yield s


async def hydrate(ids: list[str]) -> list[dict[str, Any]]:
    """B2 호환 모듈 함수 — default_reader.hydrate() 위임(Reference 등 모듈 경유)."""
    return await default_reader.hydrate(ids)


async def map_proximity(lat: float, lng: float, radius_m: int) -> dict[str, Any]:
    """B2 호환 모듈 함수 — default_reader.map_proximity() 위임(현재 호출자 없음, 예약)."""
    return await default_reader.map_proximity(lat, lng, radius_m)


__all__ = ["OnDataReader", "default_reader", "session", "hydrate", "map_proximity"]
