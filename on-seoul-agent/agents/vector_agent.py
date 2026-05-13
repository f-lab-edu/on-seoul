"""Vector Agent — 하이브리드 검색 (벡터 유사도 + BM25 전문 검색).

1. LLM으로 사용자 질의를 검색에 최적화된 문장으로 정제한다.
2. 정제된 질의를 임베딩한다.
3. vector_search(pgvector)와 bm25_search(ParadeDB)를 순차 실행한다.
   asyncpg 단일 연결은 동시 쿼리를 허용하지 않으므로 병렬 실행하지 않는다.
4. 두 결과를 RRF(Reciprocal Rank Fusion)로 결합하여 AgentState.vector_results에 저장한다.
"""

import logging

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from llm.client import get_chat_model, get_embeddings
from schemas.state import AgentState
from tools.bm25_search import bm25_search
from tools.tokenizer import tokenize_query
from tools.vector_search import vector_search

logger = logging.getLogger(__name__)

_REFINE_SYSTEM = """\
당신은 서울시 공공서비스 예약 검색 전문가입니다.
사용자 질의를 벡터 유사도 검색에 적합한 명확하고 구체적인 검색 문장으로 변환하세요.
시설 유형, 대상, 활동 특성을 포함하면 검색 품질이 높아집니다.
한국어로 2-3 문장 이내로 작성하세요.

질의에서 다음 필터 정보를 추출할 수 있으면 함께 반환하세요. 명시되지 않은 경우 null로 설정하세요.
- max_class_name: 대분류 카테고리 (예: 체육, 문화, 교육, 시설, 진료). 질의에 명확한 카테고리가 없으면 null.
- area_name: 지역구 이름 (예: 강남구, 마포구). 질의에 지역이 없으면 null.
- service_status: 예약 상태 (접수중·예약마감·접수종료·예약일시중지·안내중 중 하나). 질의에 상태가 없으면 null.
"""

_REFINE_HUMAN = "사용자 질의: {message}"

_RRF_K: int = 60  # RRF 공식 상수 (표준값 60)
_TOP_K: int = 10  # RRF 결합 결과 최대 반환 수

_ALLOWED_SERVICE_STATUSES: frozenset[str] = frozenset(["접수중", "예약마감", "접수종료", "예약일시중지", "안내중"])

# 이 서비스의 모든 문서에 공통으로 등장하는 고빈도 어휘.
# BM25는 IDF 기반이므로 전 문서에 걸쳐 빈도가 높은 단어는 IDF ≈ 0이 되어
# 스코어에 기여하지 못한다. BM25 쿼리 전송 전 이 목록으로 필터링하여
# 변별력 없는 토큰을 제거한다. 유효 토큰이 없으면 BM25를 건너뛴다.
_BM25_STOPWORDS: frozenset[str] = frozenset({
    "예약", "서울", "서울시", "공공", "서비스", "공공서비스",
    "접수", "신청", "이용", "안내", "시설", "프로그램",
})


class _RefinedQuery(BaseModel):
    refined_query: str
    max_class_name: str | None = None
    area_name: str | None = None
    service_status: str | None = None

    @field_validator("service_status", mode="before")
    @classmethod
    def _validate_service_status(cls, v: object) -> str | None:
        """LLM이 반환한 service_status가 도메인 허용 값이 아니면 None으로 대체한다."""
        if v is None:
            return None
        if v in _ALLOWED_SERVICE_STATUSES:
            return v  # type: ignore[return-value]
        return None


def _rrf_merge(
    vector_rows: list[dict],
    bm25_rows: list[dict],
    *,
    k: int = _RRF_K,
    top_k: int = 10,
) -> list[dict]:
    """Reciprocal Rank Fusion으로 두 검색 결과를 결합한다.

    각 결과 리스트에서 service_id의 순위(1-based)를 기반으로
    RRF 점수(1 / (k + rank))를 계산하고 합산한다.
    두 리스트 어느 쪽에만 있는 항목도 포함된다(FULL OUTER JOIN 의미).

    Parameters
    ----------
    vector_rows:
        vector_search 반환값. service_id, service_name, metadata, similarity 포함.
    bm25_rows:
        bm25_search 반환값. service_id, bm25_score 포함.
    k:
        RRF 공식 상수. 기본값 60.
    top_k:
        최종 반환 결과 수.

    Returns
    -------
    list[dict]
        rrf_score 내림차순 정렬된 딕셔너리 리스트.
        각 dict는 service_id, rrf_score 외에 vector_search 메타데이터를 포함한다.
    """
    scores: dict[str, float] = {}

    for rank, row in enumerate(vector_rows, start=1):
        sid = row["service_id"]
        scores[sid] = scores.get(sid, 0.0) + 1.0 / (k + rank)

    for rank, row in enumerate(bm25_rows, start=1):
        sid = row["service_id"]
        scores[sid] = scores.get(sid, 0.0) + 1.0 / (k + rank)

    # 메타데이터 인덱싱 — 벡터 결과 우선, 없으면 BM25 결과에서 보완
    # BM25 전용 결과(벡터 검색에 없는 service_id)도 메타데이터가 유지된다.
    vector_meta: dict[str, dict] = {r["service_id"]: r for r in vector_rows}
    bm25_meta: dict[str, dict] = {r["service_id"]: r for r in bm25_rows}

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    result = []
    for sid, rrf_score in merged:
        base = dict(vector_meta.get(sid, bm25_meta.get(sid, {"service_id": sid})))
        base["rrf_score"] = rrf_score
        result.append(base)

    return result


class VectorAgent:
    """질의 정제 → 임베딩 → 하이브리드 검색(벡터 + BM25) → RRF 결합 에이전트.

    ai_session : on_ai_app 계정 세션 (service_embeddings CRUD 권한)
    """

    def __init__(
        self,
        model: BaseChatModel | None = None,
        embeddings: Embeddings | None = None,
    ) -> None:
        llm = model or get_chat_model()
        self._embeddings = embeddings or get_embeddings()
        prompt = ChatPromptTemplate.from_messages([
            ("system", _REFINE_SYSTEM),
            ("human", _REFINE_HUMAN),
        ])
        self._refine_chain = prompt | llm.with_structured_output(_RefinedQuery)

    async def search(self, state: AgentState, session: AsyncSession) -> AgentState:
        """질의 정제 → 임베딩 → 하이브리드 검색 → RRF 결합.

        vector_results에 RRF 결합 결과를, refined_query에 정제된 질의를 채운
        AgentState를 반환한다.
        """
        refined: _RefinedQuery = await self._refine_chain.ainvoke(
            {"message": state["message"]}
        )

        query_vector = await self._embeddings.aembed_query(refined.refined_query)
        tokens = tokenize_query(refined.refined_query)

        # asyncpg 단일 연결은 동시 쿼리를 허용하지 않으므로 순차 실행한다.
        # TODO: 커넥션 풀에서 별도 세션을 할당하면 asyncio.gather로 병렬 실행 가능.
        #       search() 시그니처 변경(세션 주입 방식)과 함께 latency 병목 시점에 도입 검토.
        # 각 검색이 실패해도 나머지 결과로 RRF 결합이 가능하도록 독립 예외 처리한다.
        try:
            vector_rows: list[dict] = await vector_search(
                session,
                query_vector,
                max_class_name=refined.max_class_name,
                area_name=refined.area_name,
                service_status=refined.service_status,
            )
        except Exception:
            logger.warning("vector_search 실패, 빈 결과로 대체", exc_info=True)
            vector_rows = []

        # 공통어(IDF ≈ 0) 필터링 후 유효 토큰이 있을 때만 BM25 실행.
        # 전 문서 공통 어휘는 BM25 변별력에 기여하지 않으므로 제거한다.
        bm25_tokens = [t for t in tokens if t not in _BM25_STOPWORDS]
        bm25_rows: list[dict] = []
        if bm25_tokens:
            try:
                bm25_rows = await bm25_search(bm25_tokens, session)
            except Exception:
                logger.warning("bm25_search 실패, 빈 결과로 대체", exc_info=True)
        else:
            logger.debug("유효 BM25 토큰 없음 — 벡터 단독 검색으로 진행")

        merged = _rrf_merge(vector_rows, bm25_rows, top_k=_TOP_K)

        return {
            **state,
            "refined_query": refined.refined_query,
            "vector_results": merged,
        }
