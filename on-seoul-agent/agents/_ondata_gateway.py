"""on_data 읽기 게이트웨이 — data_session_ctx 세션 수명 캡슐화(B2-1/B3-1).

자원=on_data 읽기(public_service_reservations, on_data_reader). 게이트웨이는
"세션 수명·자원 선택"만 책임지고 tool 은 "쿼리 내용·SQL 안전성"을 책임진다(보완 관계).

노드 로컬 세션(제약 #2/#5): 각 메서드는 호출당 1회 acquire-use-release.
data_session_ctx() 로 풀에서 세션을 잡고 사용 후 즉시 반납한다(장수명 세션 노출 금지).

B3-1/B3-2(선택적 주입): on_data 읽기를 `OnDataReader` 클래스로 승격해 RetrievalNodes 와
ReferenceNodes 가 생성자 주입으로 받는다. 모든 on_data 호출부가 게이트웨이 주입으로
전환됨에 따라 B2 호환 모듈 함수(session/hydrate/map_proximity)는 호출자 0이 되어
퇴역시켰다(설계 기준 ⑦ 가역성 — 호출자가 사라진 예약 표면은 유지하지 않는다).

테스트 patch 타깃(tool/세션 심볼 — 주입한 OnDataReader 메서드가 경유한다):
  - 세션: agents._ondata_gateway.data_session_ctx
  - tool: agents._ondata_gateway._hydrate_services / agents._ondata_gateway._map_search
페이즈 단위 테스트는 patch 대신 가짜 OnDataReader 를 페이즈에 주입한다.
"""

from contextlib import asynccontextmanager
from typing import Any

from core.database import data_session_ctx
from tools.fetch_detail_content import fetch_detail_content as _fetch_detail_content
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

    async def fetch_detail_content(self, service_id: str) -> str | None:
        """focal service_id 단건 detail_content 원문 조회 (호출당 1회 acquire-use-release).

        운영-상세(operational_detail) prep 전용. raw 블롭은 일반 hydration 과 분리해
        focal 단건만 가져온다(블롭 격리). 발췌·정제는 호출 상류가 담당한다.
        """
        async with data_session_ctx() as s:
            return await _fetch_detail_content(s, service_id)

    async def map_proximity(
        self, lat: float, lng: float, radius_m: int
    ) -> dict[str, Any]:
        """좌표 근접 검색 — raw GeoJSON 반환까지만 (호출당 1회 acquire-use-release).

        features→ChannelData 조립은 호출 노드(map_node) 책임으로 둔다(게이트웨이는 raw 반환).
        """
        async with data_session_ctx() as s:
            return await _map_search(s, lat, lng, radius_m=radius_m)


#: 프로세스 공유 기본 reader. 게이트웨이 주입 페이즈(Retrieval/Reference)의 기본
#: 의존이 이를 공유한다(무상태이므로 단일 인스턴스 공유 안전).
default_reader = OnDataReader()


__all__ = ["OnDataReader", "default_reader"]
