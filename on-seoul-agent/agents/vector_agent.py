"""Vector Agent — 4채널 하이브리드 검색 (Phase RRF).

1. LLM으로 사용자 질의를 검색에 최적화된 문장으로 정제한다.
2. 정제된 질의를 임베딩한다.
3. 4채널을 채널별 독립 세션으로 asyncio.gather 병렬 실행한다:
   - Track A: vector_search(row_kind='identity')  + post-filter
   - Track B: vector_search(row_kind='summary')
   - Track C: question_search(row_kind='question') — PARTITION BY dedup
   - Track D: bm25_search — ParadeDB 전문 검색
4. 4채널 결과를 가중 RRF(Reciprocal Rank Fusion)로 결합한다.
5. vector_results 에는 검색 메타데이터만 채운다 ({service_id, rrf_score}).
   hydration(원본 조회)은 HydrationNode 가 단독으로 담당한다.
"""

import asyncio
import contextlib
import logging

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

import core.concurrency as _concurrency
from agents._search_channel_utils import _to_hits
from core.config import settings
from core.database import ai_session_ctx
from core.rrf import reciprocal_rank_fusion
from llm.client import get_chat_model, get_embeddings
from schemas.search import ChannelData, ChannelQuery, SearchChannel, SearchKind
from schemas.state import AgentState
from tools.bm25_search import BM25_LIMIT as _BM25_LIMIT
from tools.bm25_search import bm25_search
from tools.question_search import question_search
from tools.tokenizer import atokenize_query
from tools.vector_search import resolve_min_similarity, vector_search

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

    명시 rollback (레거시 방어 코드):
        제안 2(채널별 독립 세션)로 전환된 이후 이 세션은 이 채널 전용이며,
        async with _run_channel() 블록 종료 시 __aexit__ 가 자동으로 반납한다.
        따라서 명시 rollback 은 현재 구조에서 무해하지만 불필요하다.
        과거 공유 세션 시대(0-1)에 InFailedSQLTransactionError 연쇄 실패를 막기
        위해 추가된 코드이며, 독립 세션 전환 이후에는 삭제해도 무방하다.
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
        # 프로세스당 VectorAgent 1개이므로 실질 프로세스 전역 cap.
        # self._channel_sema 는 VectorAgent 싱글톤에 1개이므로, 요청이 몇 개
        # 들어와도 동시에 ai_session 을 획득할 수 있는 채널 수의 상한은
        # vector_channel_concurrency(4) 다. 인스턴스 레벨로 유지해야 이 cap이
        # 프로세스 전역으로 작동한다(search() 호출마다 새 Semaphore를 생성하면
        # per-request 4 상한이 되어 전역 cap 역할을 하지 못한다).
        self._channel_sema = asyncio.Semaphore(settings.vector_channel_concurrency)

    async def search(
        self,
        state: AgentState,
    ) -> dict:
        """질의 정제 → 임베딩 → 4채널 병렬 검색 → 가중 RRF.

        채널마다 독립 ai_session_ctx()로 세션을 열어 asyncio.gather로 동시 실행한다.
        asyncio.Semaphore(vector_channel_concurrency)로 동시 채널 수를 cap한다.

        vector_results 에는 검색 메타데이터만 채운다:
          [{service_id, rrf_score}, ...]
        원본 데이터 hydration 은 HydrationNode 가 단독으로 담당한다.

        Router가 이미 refined_query와 post-filter를 산출한 경우
        (state["refined_query"] 존재), 중복 LLM 호출을 피하기 위해 refine 체인을 skip하고
        state["max_class_name"/"area_name"/"service_status"] 값을 그대로 사용한다.
        """
        plan = state.get("plan") or {}
        filters = state.get("filters") or {}
        router_refined = plan.get("refined_query")
        if router_refined:
            refined = _RefinedQuery(
                refined_query=router_refined,
                max_class_name=filters.get("max_class_name"),
                area_name=filters.get("area_name"),
                service_status=filters.get("service_status"),
            )
        else:
            refined = await self._refine_chain.ainvoke({"message": state["message"]})

        query_vector = await self._embeddings.aembed_query(refined.refined_query)
        # kiwipiepy.tokenize()는 동기 C 확장 — asyncio.to_thread()로 오프로드.
        tokens = await atokenize_query(refined.refined_query)
        bm25_tokens = [t for t in tokens if t not in _BM25_STOPWORDS]

        # 4채널 병렬 실행.
        # 채널마다 독립 ai_session_ctx()로 세션을 열어 asyncpg 동시 쿼리 제약을 우회한다.
        # self._channel_sema(인스턴스 레벨)로 동시 채널 수를 cap하여 풀 버스트를 방지한다.
        # bm25_tokens 없으면 채널 D를 태스크에 포함하지 않고 빈 결과로 인덱스를 고정한다.
        # 결과 인덱스: results[0]=a_rows, results[1]=b_rows, results[2]=c_rows, results[3]=d_rows
        #
        # 글로벌 세마포어(core.concurrency.vector_global_sema) — 외곽 가드.
        #   on_ai 풀(cap=25)이 고갈되지 않도록 프로세스 전체 동시 채널 수를 제한한다.
        #   100 동시 요청 × 4채널 = 400 잠재 쿼리를 vector_global_concurrency(기본 20)로 캡.
        #   채널별 세마포어(self._channel_sema=4)와 중첩: 글로벌이 외곽, 채널이 내곽.
        #   모듈 속성(_concurrency.vector_global_sema)을 런타임에 읽어 lifespan 이후
        #   init_global_sema()로 할당된 값을 정확히 참조한다.

        async def _run_channel(coro_fn):
            # lifespan 이전(테스트 환경)에는 vector_global_sema가 None이므로
            # contextlib.nullcontext()로 대체하여 분기 중복을 제거한다.
            sema_ctx = _concurrency.vector_global_sema or contextlib.nullcontext()
            async with sema_ctx:
                async with self._channel_sema:
                    async with ai_session_ctx() as session:
                        return await coro_fn(session)

        tasks = [
            _run_channel(
                lambda s: _safe_vector_search(
                    s,
                    query_vector,
                    row_kind="identity",
                    max_class_name=refined.max_class_name,
                    area_name=refined.area_name,
                    service_status=refined.service_status,
                )
            ),
            _run_channel(
                lambda s: _safe_vector_search(s, query_vector, row_kind="summary")
            ),
            _run_channel(lambda s: _safe_question_search(s, query_vector)),
        ]
        if bm25_tokens:
            tasks.append(
                _run_channel(lambda s: _safe_bm25_search(s, bm25_tokens))
            )

        gathered = await asyncio.gather(*tasks)
        a_rows: list[dict] = gathered[0]
        b_rows: list[dict] = gathered[1]
        c_rows: list[dict] = gathered[2]
        d_rows: list[dict] = gathered[3] if bm25_tokens else []

        if not bm25_tokens:
            logger.debug("유효 BM25 토큰 없음 — 벡터 단독 검색으로 진행")

        # 가중치 결정
        sub_intent = plan.get("vector_sub_intent")
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

        # --- search_channels 구성 (5채널) ---
        # 트랙별 실제 운영값(config)을 그대로 기록한다.
        track_top_k = settings.vector_track_top_k
        search_channels: dict[str, ChannelData] = {
            SearchChannel.VECTOR_A: ChannelData(
                kind=SearchKind.VECTOR,
                query=ChannelQuery(
                    query_text=refined.refined_query,
                    parameters={
                        "row_kind": "identity",
                        "top_k": track_top_k,
                        "min_similarity": resolve_min_similarity("identity"),
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
                        "top_k": track_top_k,
                        "min_similarity": resolve_min_similarity("summary"),
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
                        "top_k": track_top_k,
                        "min_similarity": resolve_min_similarity("question"),
                    },
                ),
                hits=_to_hits(c_rows, score_field="similarity"),
            ),
            SearchChannel.BM25: ChannelData(
                kind=SearchKind.BM25,
                query=ChannelQuery(
                    query_text=" ".join(bm25_tokens) if bm25_tokens else None,
                    parameters={"tokens": bm25_tokens, "top_k": _BM25_LIMIT},
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
            "plan": {"refined_query": refined.refined_query},
            "vector": {"results": meta_results},
            "search_channels": search_channels,
        }
