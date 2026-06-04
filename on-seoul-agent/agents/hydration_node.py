"""HydrationNode — 검색 결과 service_id → public_service_reservations 원본 통합 슬롯.

책임 분리 동기
--------------
임베딩 검색(`service_embeddings`, on_ai)과 정형 데이터(`public_service_reservations`,
on_data)는 저장 위치·스키마·DB 계정이 모두 다르다. 검색 노드가 원본 조회까지 떠안으면
다음과 같은 결합 문제가 생긴다.

- `VectorAgent` 가 ai_session(검색)과 data_session(원본 조회) 두 세션을 모두 받아야 한다.
- 검색 노드별 출력 형식이 달라 후속 단계(`AnswerAgent`)가 분기 처리해야 한다.
- 미래의 새 검색 경로(단독 BM25, hybrid v2 등) 추가 시 hydration 코드가 중복된다.

본 노드는 단일 책임으로 그 결합을 해소한다.

1. 검색 결과(`vector_results` / `sql_results`)에서 service_id 추출 (`_extract_service_ids`).
2. VECTOR_SEARCH: `hydrate_services` 호출 + 검색 메타 머지 → `hydrated_services` 슬롯.
3. SQL_SEARCH: `sql_results` 가 이미 원본이므로 그대로 통과.

설계 원칙 — 별도 슬롯 없이 State 단일 진실원
-------------------------------------------
`pending_service_ids` 같은 별도 입력 슬롯을 두지 않는다. service_id 는 이미
`vector_results` / `sql_results` 의 각 행에 키로 존재하므로, intent 만 보고 추출 함수로
뽑으면 충분하다.

SQL_SEARCH 경로 처리
--------------------
`sql_search` 는 한 SELECT 로 원본을 직접 반환하므로 `sql_results` 가 이미 hydrated 다.
따라서 본 노드는 SQL 경로에서는 추가 조회 없이 그대로 통과시킨다.
"""

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from schemas.state import AgentState, IntentType
from tools.hydrate_services import hydrate_services

logger = logging.getLogger(__name__)


def _extract_service_ids(state: AgentState) -> list[str]:
    """라우팅 intent 에 따라 검색 결과에서 service_id 리스트를 추출한다.

    검색 결과 행 순서를 보존하므로 hydration 후에도 검색 랭킹이 유지된다.

    Returns:
        intent 가 VECTOR_SEARCH / SQL_SEARCH 가 아니거나, 해당 결과 슬롯이 비어 있으면 []
    """
    intent = state.get("intent")
    if intent == IntentType.VECTOR_SEARCH:
        rows = state.get("vector_results") or []
    elif intent == IntentType.SQL_SEARCH:
        rows = state.get("sql_results") or []
    else:
        return []
    return [r["service_id"] for r in rows if r.get("service_id")]


def _payment_matches(row_value: object, payment_type: str) -> bool:
    """payment_type 매칭 규칙 — 무료=정확, 유료=접두("유료"로 시작).

    원천 데이터의 "유료(요금안내문의)" 변형은 접두 매칭으로 포괄한다.
    """
    if not isinstance(row_value, str):
        return False
    if payment_type == "무료":
        return row_value == "무료"
    if payment_type == "유료":
        return row_value.startswith("유료")
    return True


def _filter_by_payment(rows: list[dict], payment_type: str | None) -> list[dict]:
    """hydrated 원본 행을 payment_type 으로 post-filter 한다.

    벡터 검색 metadata 에는 payment_type 이 포함되지 않으므로(재임베딩 회피),
    hydration 직후 원본 컬럼(payment_type)으로 정확히 거른다.
    payment_type 이 None 이면 필터를 적용하지 않고 그대로 반환한다.
    """
    if payment_type is None:
        return rows
    return [r for r in rows if _payment_matches(r.get("payment_type"), payment_type)]


def _merge_search_meta(hydrated: list[dict], source_rows: list[dict]) -> list[dict]:
    """검색 단계가 함께 산출한 메타데이터(rrf_score 등)를 원본 행과 머지.

    hydrated 원본 row 에 없는 키만 채운다 (원본 우선). 검색 메타 키는 도메인 컬럼명과
    겹치지 않도록 검색 단계에서 미리 정의된다 (예: rrf_score, similarity, bm25_score).
    """
    meta_by_id = {r["service_id"]: r for r in source_rows if r.get("service_id")}
    for row in hydrated:
        sid = row.get("service_id")
        if sid is None or sid not in meta_by_id:
            continue
        for key, val in meta_by_id[sid].items():
            if key not in row:
                row[key] = val
    return hydrated


class HydrationNode:
    """검색 결과 service_id → 원본 데이터 통합 슬롯 매퍼.

    그래프 배치: `sql_node` / `vector_node` 직후, `answer_node` 직전.

    상태 변화:
        - intent=VECTOR_SEARCH → service_id 추출 → hydrate_services 호출 → 메타 머지.
        - intent=SQL_SEARCH    → sql_results 그대로 통과 (sql_search 가 이미 원본 반환).
        - 기타 intent          → hydrated_services = [].

    재호출 안전(idempotent):
        hydrated_services 가 None 이 아닌 값(빈 리스트 포함)으로 이미 설정돼 있으면
        재실행하지 않고 그대로 둔다. None 은 "미설정 또는 retry_prep_node 에 의한 리셋"을
        의미하며, 이 경우만 hydration 을 재실행한다.
    """

    async def __call__(
        self,
        state: AgentState,
        data_session: AsyncSession,
    ) -> dict[str, Any]:
        # 재호출 안전 — None 이 아니면(빈 리스트 포함) 이미 처리된 상태이므로 skip.
        # retry_prep_node 는 hydrated_services=None 으로 명시 리셋하므로
        # retry 경로에서 [] 상태가 가드를 통과해 중복 실행되는 문제가 없다.
        if state.get("hydrated_services") is not None:
            return {}

        intent = state.get("intent")

        # SQL_SEARCH — sql_results 가 이미 원본 행이므로 그대로 통과.
        if intent == IntentType.SQL_SEARCH:
            sql_results = state.get("sql_results") or []
            return {"hydrated_services": list(sql_results)}

        # VECTOR_SEARCH — service_id 추출 + hydrate_services 호출 + 검색 메타 머지.
        if intent == IntentType.VECTOR_SEARCH:
            service_ids = _extract_service_ids(state)
            if not service_ids:
                return {"hydrated_services": []}
            try:
                hydrated = await hydrate_services(data_session, service_ids)
            except Exception:
                logger.warning(
                    "hydrate_services 실패 — 빈 결과 fallback (service_ids=%d건)",
                    len(service_ids),
                    exc_info=True,
                )
                return {"hydrated_services": []}
            source_rows = state.get("vector_results") or []
            hydrated = _merge_search_meta(hydrated, source_rows)
            # 벡터 경로 payment 필터 — metadata 에 payment_type 이 없으므로
            # hydration 직후 원본 컬럼으로 post-filter (무료=정확/유료=접두).
            #
            # recall 주의: 이 필터는 vector_node 의 rrf_top_k_final 절단 "이후"에
            # 적용된다. 상위 후보 대부분이 반대 결제유형이면 카드가 top_k 보다 적게
            # 남는 recall 손실이 발생할 수 있다. 현재는 동작을 단순하게 유지하고
            # (절단 전 넉넉히 확보 후 필터링하는 방식은 범위가 커 후속 과제로 남김),
            # payment 필터가 결과 수를 줄일 수 있음을 명시만 한다.
            hydrated = _filter_by_payment(hydrated, state.get("payment_type"))
            return {"hydrated_services": hydrated}

        # MAP / FALLBACK — hydration 대상 아님 (MAP 은 GeoJSON 구조라 별도 처리).
        return {"hydrated_services": []}
