"""검색 채널 관측(observability) 스키마.

chat_search_queries / chat_search_results 테이블의 Python 표현.
AgentState.search_channels 의 값 타입을 정의하고,
kind / channel 을 코드 상수로 관리하여 typo 를 컴파일 타임에 방지한다.
"""

from typing import Any, Final, TypedDict


# =============================================================================
# 채널 단위 데이터 구조
# =============================================================================


class ChannelHit(TypedDict):
    """채널 검색 결과 1건 (chat_search_results 1행)."""

    rank: int               # 1-based 순위
    service_id: str
    score: float | None     # 채널 native 점수. SQL 채널 등 점수 없으면 None
    meta: dict[str, Any]    # 채널별 부가 정보. 빈 dict 허용


class ChannelQuery(TypedDict):
    """채널 검색 입력 (chat_search_queries 1행)."""

    query_text: str | None   # 임베딩 텍스트 / SQL keyword / BM25 토큰 join 등.
                             # rrf / final 채널은 원본 검색 미수행이므로 None.
    parameters: dict[str, Any]  # 구조화 파라미터 (top_k, filters, weights 등)


class ChannelData(TypedDict):
    """채널 1개의 입력(query)과 출력(hits)을 한 묶음으로 보관.

    AgentState.search_channels 의 값 타입.
    kind + query + hits 를 함께 두어 짝을 잃지 않는다.
    """

    kind: str               # SearchKind 상수 값 중 하나
    query: ChannelQuery
    hits: list[ChannelHit]


# =============================================================================
# kind / channel 상수
# =============================================================================


class SearchKind:
    """kind 화이트리스트. DB CHECK (chat_search_queries, chat_search_results) 와 동기화 유지.

    kind 가 새로 필요해지면:
      1. DB: ALTER TABLE ... ADD CONSTRAINT ... CHECK (kind IN (..., 'new_kind'));
      2. 코드: 이 클래스에 상수 추가 + _CHANNEL_TO_KIND 에 매핑 추가.
    """

    SQL: Final[str] = "sql"
    VECTOR: Final[str] = "vector"
    BM25: Final[str] = "bm25"
    RRF: Final[str] = "rrf"
    MAP: Final[str] = "map"
    FINAL: Final[str] = "final"


class SearchChannel:
    """채널명 상수. DB 에는 CHECK 없지만 코드에서는 이 상수만 사용한다.

    새 채널 추가는 DB 마이그레이션 없이 이 클래스에만 추가하면 된다.
    kind 매핑이 필요하므로 _CHANNEL_TO_KIND 에도 함께 추가할 것.
    """

    # kind=sql
    SQL: Final[str] = "sql"

    # kind=vector
    VECTOR: Final[str] = "vector"           # Phase 1 단일 경쟁
    VECTOR_A: Final[str] = "vector_a"       # Phase 2+ (post-filter A)
    VECTOR_B: Final[str] = "vector_b"       # Phase 2+ (post-filter B)
    VECTOR_C: Final[str] = "vector_c"       # Phase 2+ (intent 분류 트랙)
    HYDE_VECTOR: Final[str] = "hyde_vector" # Phase 3+ (HyDE 생성 후 임베딩)

    # kind=bm25
    BM25: Final[str] = "bm25"

    # kind=rrf
    RRF: Final[str] = "rrf"

    # kind=map
    MAP: Final[str] = "map"

    # kind=final
    FINAL: Final[str] = "final"


# channel → kind 매핑 테이블.
# 직접 참조하지 말고 kind_of() 헬퍼를 통해 사용한다.
_CHANNEL_TO_KIND: dict[str, str] = {
    SearchChannel.SQL: SearchKind.SQL,
    SearchChannel.VECTOR: SearchKind.VECTOR,
    SearchChannel.VECTOR_A: SearchKind.VECTOR,
    SearchChannel.VECTOR_B: SearchKind.VECTOR,
    SearchChannel.VECTOR_C: SearchKind.VECTOR,
    SearchChannel.HYDE_VECTOR: SearchKind.VECTOR,
    SearchChannel.BM25: SearchKind.BM25,
    SearchChannel.RRF: SearchKind.RRF,
    SearchChannel.MAP: SearchKind.MAP,
    SearchChannel.FINAL: SearchKind.FINAL,
}


def search_channels_reducer(
    old: "dict[str, ChannelData] | None",
    new: "dict[str, ChannelData] | None",
) -> "dict[str, ChannelData]":
    """LangGraph reducer for AgentState.search_channels.

    리셋 시그널: new 가 None 이거나 빈 dict {} 이면 채널 맵을 완전 초기화한다.
    retry_prep_node 가 self-correction 재시도 전에 `{"search_channels": {}}` 를 반환하여
    이전 시도의 채널 데이터를 지우고 UNIQUE 제약 위반을 예방한다.

    정상 merge: new 가 비어있지 않으면 기존 dict 에 or_ 병합한다.
    각 노드는 자기 채널 키만 담은 부분 dict 를 반환하므로, 동일 채널 키가 재등장하면
    최신 데이터로 덮어쓴다 (재시도 시 마지막 시도 데이터만 남음).

    CONTRACT (반드시 지킬 것):
        노드가 채널을 추가할 게 없으면 반환 dict 에서 `search_channels` 키를 **생략** 해야 한다.
        `{"search_channels": {}}` 를 반환하면 리셋으로 처리되어 기존 채널 데이터가 전부 지워진다.
        의도적 리셋은 오직 `retry_prep_node` 만 수행한다.

    DANGER: `{}` (빈 dict) 또는 `None` 은 리셋 시그널이다. 실수로 반환하지 않도록 주의.
    """
    if not new:  # {} 또는 None → 완전 리셋 (DANGER: 의도적인 경우만)
        return {}
    return (old or {}) | new


def kind_of(channel: str) -> str:
    """채널명에서 kind 를 조회한다.

    미등록 채널은 ValueError 를 raise 하여 typo 를 조기에 감지한다.
    새 채널 추가 시 _CHANNEL_TO_KIND 에 매핑을 추가해야 한다.

    Args:
        channel: SearchChannel 상수 값 (예: "vector_a", "bm25")

    Returns:
        SearchKind 상수 값 (예: "vector", "bm25")

    Raises:
        ValueError: 미등록 채널명
    """
    try:
        return _CHANNEL_TO_KIND[channel]
    except KeyError:
        raise ValueError(f"unknown channel: {channel!r}. _CHANNEL_TO_KIND 에 매핑을 추가하세요.")
