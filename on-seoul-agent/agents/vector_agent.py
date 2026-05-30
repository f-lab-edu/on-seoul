"""Vector Agent — 4채널 하이브리드 검색 (Phase RRF).

1. LLM으로 사용자 질의를 검색에 최적화된 문장으로 정제한다.
2. 정제된 질의를 임베딩한다.
3. 4채널을 순차 실행한다 (asyncpg 단일 세션 제약):
   - Track A: vector_search(row_kind='identity')  + post-filter
   - Track B: vector_search(row_kind='summary')
   - Track C: question_search(row_kind='question') — PARTITION BY dedup
   - Track D: bm25_search — ParadeDB 전문 검색
4. 4채널 결과를 가중 RRF(Reciprocal Rank Fusion)로 결합한다.
5. vector_results 에는 검색 메타데이터만 채운다 ({service_id, rrf_score}).
   hydration(원본 조회)은 HydrationNode 가 단독으로 담당한다.
"""

import logging

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from agents._search_channel_utils import _to_hits
from core.config import settings
from core.rrf import reciprocal_rank_fusion
from llm.client import get_chat_model, get_embeddings
from schemas.search import ChannelData, ChannelQuery, SearchChannel, SearchKind
from schemas.state import AgentState
from tools.bm25_search import bm25_search
from tools.question_search import question_search
from tools.tokenizer import tokenize_query
from tools.vector_search import MIN_SIMILARITY, TOP_K as _VECTOR_TOP_K
from tools.vector_search import vector_search

logger = logging.getLogger(__name__)

_REFINE_SYSTEM = """\
당신은 서울시 공공서비스 예약 검색 전문가입니다.
사용자 질의를 벡터 유사도 검색에 적합한 명확하고 구체적인 검색 문장으로 변환하세요.
시설 유형, 대상, 활동 특성을 포함하면 검색 품질이 높아집니다.
한국어로 2-3 문장 이내로 작성하세요.

질의에서 다음 필터 정보를 추출할 수 있으면 함께 반환하세요. 명시되지 않은 경우 null로 설정하세요.
- max_class_name: 대분류 카테고리. 체육시설·문화체험·공간시설·교육강좌·진료복지 중 하나. 질의에 명확한 카테고리가 없으면 null.
- area_name: 지역구 이름 (예: 강남구, 마포구). 질의에 지역이 없으면 null.
- service_status: 예약 상태 (접수중·예약마감·접수종료·예약일시중지·안내중 중 하나). 질의에 상태가 없으면 null.
"""

_REFINE_HUMAN = "사용자 질의: {message}"

_RRF_K: int = 60  # RRF 공식 상수 (표준값 60)
_TOP_K: int = 10  # RRF 결합 결과 최대 반환 수

_ALLOWED_SERVICE_STATUSES: frozenset[str] = frozenset(
    ["접수중", "예약마감", "접수종료", "예약일시중지", "안내중"]
)

# 이 서비스의 모든 문서에 공통으로 등장하는 고빈도 어휘.
# BM25는 IDF 기반이므로 전 문서에 걸쳐 빈도가 높은 단어는 IDF ≈ 0이 되어
# 스코어에 기여하지 못한다. BM25 쿼리 전송 전 이 목록으로 필터링하여
# 변별력 없는 토큰을 제거한다. 유효 토큰이 없으면 BM25를 건너뛴다.
_BM25_STOPWORDS: frozenset[str] = frozenset(
    {
        "예약",
        "서울",
        "서울시",
        "공공",
        "서비스",
        "공공서비스",
        "접수",
        "신청",
        "이용",
        "안내",
        "시설",
        "프로그램",
    }
)


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

    Phase 1 호환 함수. 기존 test_vector_agent.py에서 직접 사용.
    Phase RRF에서는 core.rrf.reciprocal_rank_fusion을 사용하되,
    이 함수는 하위 호환성을 위해 유지한다.

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
    vector_meta: dict[str, dict] = {r["service_id"]: r for r in vector_rows}
    bm25_meta: dict[str, dict] = {r["service_id"]: r for r in bm25_rows}

    merged = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]

    result = []
    for sid, rrf_score in merged:
        base = dict(vector_meta.get(sid, bm25_meta.get(sid, {"service_id": sid})))
        base["rrf_score"] = rrf_score
        result.append(base)

    return result


def _resolve_weights(sub_intent: str | None) -> dict[str, float] | None:
    """vector_sub_intent → RRF 가중치 프로파일 반환.

    rrf_unweighted_baseline=True(기본값)이면 항상 None을 반환하여
    unweighted RRF(모든 채널 가중치 1.0)를 사용한다.

    rrf_unweighted_baseline=False이면:
    - vector_sub_intent_enabled=True: sub_intent로 프로파일 선택.
    - vector_sub_intent_enabled=False: vector_default_sub_intent 프로파일 사용.
    - 허용되지 않는 sub_intent: vector_default_sub_intent 프로파일로 폴백.
    """
    if settings.rrf_unweighted_baseline:
        return None

    if not settings.vector_sub_intent_enabled:
        label = settings.vector_default_sub_intent
    else:
        label = sub_intent or settings.vector_default_sub_intent

    _fallback = settings.rrf_weight_profiles.get(
        settings.vector_default_sub_intent,
        {"track_a": 1.0, "track_b": 1.0, "track_c": 1.0, "bm25": 1.0},
    )
    return settings.rrf_weight_profiles.get(label, _fallback)


async def _safe_vector_search(
    session: AsyncSession, query_vector: list[float], **kwargs
) -> list[dict]:
    """vector_search 예외를 격리하여 빈 결과 반환."""
    try:
        return await vector_search(session, query_vector, **kwargs)
    except Exception:
        logger.warning("vector_search 실패, 빈 결과로 대체", exc_info=True)
        return []


async def _safe_question_search(
    session: AsyncSession, query_vector: list[float]
) -> list[dict]:
    """question_search 예외를 격리하여 빈 결과 반환."""
    try:
        return await question_search(session, query_vector)
    except Exception:
        logger.warning("question_search 실패, 빈 결과로 대체", exc_info=True)
        return []


async def _safe_bm25_search(session: AsyncSession, tokens: list[str]) -> list[dict]:
    """bm25_search 예외를 격리하여 빈 결과 반환.

    예외 발생 시 세션 트랜잭션을 롤백하여 이후 쿼리(search_persist 등)가
    InFailedSQLTransactionError 로 연쇄 실패하지 않도록 복구한다.
    """
    try:
        return await bm25_search(tokens, session)
    except Exception:
        logger.warning("bm25_search 실패, 빈 결과로 대체", exc_info=True)
        try:
            await session.rollback()
        except Exception:
            pass
        return []


class VectorAgent:
    """질의 정제 → 임베딩 → 4채널 병렬 검색 → 가중 RRF → hydration 에이전트.

    ai_session : on_ai_app 계정 세션 (service_embeddings CRUD 권한)
    data_session: on_data_reader 계정 세션 (public_service_reservations SELECT 전용)
    """

    def __init__(
        self,
        model: BaseChatModel | None = None,
        embeddings: Embeddings | None = None,
    ) -> None:
        llm = model or get_chat_model()
        self._embeddings = embeddings or get_embeddings()
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", _REFINE_SYSTEM),
                ("human", _REFINE_HUMAN),
            ]
        )
        self._refine_chain = prompt | llm.with_structured_output(_RefinedQuery)

    async def search(
        self,
        state: AgentState,
        ai_session: AsyncSession,
    ) -> dict:
        """질의 정제 → 임베딩 → 4채널 순차 검색 → 가중 RRF.

        ai_session : service_embeddings(on_ai)에 대한 의미 검색·BM25 용도

        vector_results 에는 검색 메타데이터만 채운다:
          [{service_id, rrf_score}, ...]
        원본 데이터 hydration 은 HydrationNode 가 단독으로 담당한다.

        Router가 이미 refined_query와 post-filter를 산출한 경우
        (state["refined_query"] 존재), 중복 LLM 호출을 피하기 위해 refine 체인을 skip하고
        state["max_class_name"/"area_name"/"service_status"] 값을 그대로 사용한다.
        """
        router_refined = state.get("refined_query")
        if router_refined:
            refined = _RefinedQuery(
                refined_query=router_refined,
                max_class_name=state.get("max_class_name"),
                area_name=state.get("area_name"),
                service_status=state.get("service_status"),
            )
        else:
            refined = await self._refine_chain.ainvoke({"message": state["message"]})

        query_vector = await self._embeddings.aembed_query(refined.refined_query)
        tokens = tokenize_query(refined.refined_query)
        bm25_tokens = [t for t in tokens if t not in _BM25_STOPWORDS]

        # 4채널 순차 실행.
        # asyncpg 단일 세션은 동시 쿼리를 허용하지 않으므로 순차 실행한다.
        # 각 _safe_* 래퍼가 예외를 개별 격리하므로 한 채널 실패가 전체에 영향을 주지 않는다.
        a_rows = await _safe_vector_search(
            ai_session,
            query_vector,
            row_kind="identity",
            max_class_name=refined.max_class_name,
            area_name=refined.area_name,
            service_status=refined.service_status,
        )
        b_rows = await _safe_vector_search(ai_session, query_vector, row_kind="summary")
        c_rows = await _safe_question_search(ai_session, query_vector)
        if bm25_tokens:
            d_rows = await _safe_bm25_search(ai_session, bm25_tokens)
        else:
            d_rows = []
            logger.debug("유효 BM25 토큰 없음 — 벡터 단독 검색으로 진행")

        # 가중치 결정
        sub_intent = state.get("vector_sub_intent")
        weights = _resolve_weights(sub_intent)

        # 4채널 RRF 결합
        merged = reciprocal_rank_fusion(
            {
                "track_a": [r["service_id"] for r in a_rows],
                "track_b": [r["service_id"] for r in b_rows],
                "track_c": [r["service_id"] for r in c_rows],
                "bm25": [r["service_id"] for r in d_rows],
            },
            weights=weights,
            k_constant=settings.rrf_k_constant,
        )

        # vector_results: 메타데이터 only — hydration 은 HydrationNode 책임
        rrf_top = merged[: settings.rrf_top_k_final]
        meta_results: list[dict] = [
            {"service_id": sid, "rrf_score": score} for sid, score in rrf_top
        ]

        # --- search_channels 구성 (6채널) ---
        search_channels: dict[str, ChannelData] = {
            SearchChannel.VECTOR_A: ChannelData(
                kind=SearchKind.VECTOR,
                query=ChannelQuery(
                    query_text=refined.refined_query,
                    parameters={
                        "row_kind": "identity",
                        "top_k": _VECTOR_TOP_K,
                        "min_similarity": MIN_SIMILARITY,
                        "max_class_name": refined.max_class_name,
                        "area_name": refined.area_name,
                        "service_status": refined.service_status,
                    },
                ),
                hits=_to_hits(a_rows, score_field="similarity"),
            ),
            SearchChannel.VECTOR_B: ChannelData(
                kind=SearchKind.VECTOR,
                query=ChannelQuery(
                    query_text=refined.refined_query,
                    parameters={
                        "row_kind": "summary",
                        "top_k": _VECTOR_TOP_K,
                        "min_similarity": MIN_SIMILARITY,
                    },
                ),
                hits=_to_hits(b_rows, score_field="similarity"),
            ),
            SearchChannel.VECTOR_C: ChannelData(
                kind=SearchKind.VECTOR,
                query=ChannelQuery(
                    query_text=refined.refined_query,
                    parameters={
                        "row_kind": "question",
                        "top_k": _VECTOR_TOP_K,
                        "min_similarity": MIN_SIMILARITY,
                    },
                ),
                hits=_to_hits(c_rows, score_field="similarity"),
            ),
            SearchChannel.BM25: ChannelData(
                kind=SearchKind.BM25,
                query=ChannelQuery(
                    query_text=" ".join(bm25_tokens) if bm25_tokens else None,
                    parameters={"tokens": bm25_tokens, "top_k": _VECTOR_TOP_K},
                ),
                hits=_to_hits(d_rows, score_field="bm25_score"),
            ),
            SearchChannel.RRF: ChannelData(
                kind=SearchKind.RRF,
                query=ChannelQuery(
                    query_text=None,
                    parameters={
                        "source_channels": [
                            SearchChannel.VECTOR_A,
                            SearchChannel.VECTOR_B,
                            SearchChannel.VECTOR_C,
                            SearchChannel.BM25,
                        ],
                        "weights": weights,
                        "k_constant": settings.rrf_k_constant,
                    },
                ),
                hits=_to_hits(
                    [{"service_id": sid, "rrf_score": score} for sid, score in merged],
                    score_field="rrf_score",
                ),
            ),
        }

        return {
            "refined_query": refined.refined_query,
            "vector_results": meta_results,
            "search_channels": search_channels,
        }
