"""Vector Agent — 4채널 하이브리드 검색 (Phase RRF).

1. LLM으로 사용자 질의를 검색에 최적화된 문장으로 정제한다.
2. 정제된 질의를 임베딩한다.
3. 4채널을 채널별 독립 세션으로 asyncio.gather 병렬 실행한다:
   - Track A: vector_search(row_kind='identity')  + post-filter
   - Track B: vector_search(row_kind='summary')
   - Track C: question_search(row_kind='question') — PARTITION BY dedup
   - Track D: bm25_search — ParadeDB 전문 검색
4. 4채널 결과를 가중 RRF(Reciprocal Rank Fusion)로 결합한다.
5. vector.results 에는 검색 메타데이터만 채운다 ({service_id, rrf_score}).
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
from agents.router_agent import SEOUL_DISTRICTS, normalize_max_class_name
from tools.target_audience import ALLOWED_AUDIENCES
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
- max_class_name: 대분류 카테고리 배열. 체육시설·문화체험·공간시설·교육강좌·진료복지 중에서 고른다. 항상 배열로 반환하고, 없으면 null. "체육시설 말고/빼고/제외" 같은 부정 표현이면 그 카테고리를 뺀 나머지 4종을 모두 배열에 담는다(예: "체육시설 말고" → ["문화체험","공간시설","교육강좌","진료복지"]).
- area_name: 지역구 이름 배열 (예: ["강남구"], 여러 지역이면 ["성동구","광진구"]). 항상 배열로 반환하고, 질의에 지역이 없으면 null.
- service_status: 예약 상태 (접수중·예약마감·접수종료·예약일시중지·안내중 중 하나). 질의에 상태가 없으면 null.
- target_audience: 대상 그룹. CHILD/ADULT/SENIOR/FAMILY 중 하나. 대상(아이·성인·어르신·가족)이 명시되면 매핑하고, 없으면 null. 자유 텍스트 금지.
"""

_REFINE_HUMAN = "사용자 질의: {message}"

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
    # 다중 카테고리 — "체육시설 말고"는 여집합(5종−X) 배열. router 와 동일 정규화.
    max_class_name: list[str] | None = None
    area_name: list[str] | None = None
    service_status: str | None = None
    target_audience: str | None = None

    @field_validator("max_class_name", mode="before")
    @classmethod
    def _validate_max_class_name(cls, v: object) -> list[str] | None:
        """max_class_name 을 닫힌 5종 리스트로 정규화한다(router 와 단일 출처 공유)."""
        return normalize_max_class_name(v)

    @field_validator("service_status", mode="before")
    @classmethod
    def _validate_service_status(cls, v: object) -> str | None:
        """LLM이 반환한 service_status가 도메인 허용 값이 아니면 None으로 대체한다."""
        if v is None:
            return None
        if v in _ALLOWED_SERVICE_STATUSES:
            return v  # type: ignore[return-value]
        return None

    @field_validator("area_name", mode="before")
    @classmethod
    def _validate_area_name(cls, v: object) -> list[str] | None:
        """area_name 을 자치구 화이트리스트 리스트로 정규화한다(단일/배열 흡수)."""
        if v is None:
            return None
        candidates = [v] if isinstance(v, str) else v
        if not isinstance(candidates, (list, tuple)):
            return None
        valid = [c for c in candidates if isinstance(c, str) and c in SEOUL_DISTRICTS]
        return valid or None

    @field_validator("target_audience", mode="before")
    @classmethod
    def _validate_target_audience(cls, v: object) -> str | None:
        """target_audience 를 허용 enum(CHILD/ADULT/SENIOR/FAMILY)으로 강제한다."""
        if isinstance(v, str) and v in ALLOWED_AUDIENCES:
            return v
        return None


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
    """bm25_search 예외를 격리하여 빈 결과 반환."""
    try:
        return await bm25_search(tokens, session)
    except Exception:
        logger.warning("bm25_search 실패, 빈 결과로 대체", exc_info=True)
        return []


async def _run_channel(coro_fn):
    """채널 1개를 글로벌 세마포어 + 독립 ai_session_ctx() 안에서 실행한다.

    lifespan 이전(테스트·스크립트)에는 vector_global_sema가 None이므로
    contextlib.nullcontext()로 대체한다.

    세마포어/세션 획득 + 검색 전체를 try로 감싸 어떤 예외든 그 채널만
    빈 리스트로 떨어뜨린다(옵션 b). _safe_* 래퍼는 검색 함수 내부 예외만
    흡수하지만, 풀 고갈 시 ai_session_ctx() 세션 획득에서 발생하는
    TimeoutError(QueuePool/asyncpg)는 _safe_* 바깥이므로 여기서 격리해야
    gather(return_exceptions=False)로 전파돼 요청 전체 벡터 검색이
    실패하거나 다른 채널이 orphan으로 남는 것을 막는다.
    CancelledError는 Exception 비상속(3.8+)이라 정상 취소 전파는 막지 않는다.
    """
    sema_ctx = _concurrency.vector_global_sema or contextlib.nullcontext()
    try:
        async with sema_ctx:
            async with ai_session_ctx() as session:
                return await coro_fn(session)
    except Exception:
        logger.warning("채널 세션 획득/실행 실패 — 빈 결과로 대체", exc_info=True)
        return []


async def run_parallel_channels(
    query_vector: list[float],
    bm25_tokens: list[str],
    *,
    max_class_name: list[str] | None = None,
    area_name: list[str] | None = None,
    service_status: str | None = None,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """4채널 retrieval 팬아웃을 채널별 독립 세션 + asyncio.gather로 병렬 실행한다.

    임베딩·토크나이징은 호출자가 미리 수행해 주입한다(이 함수는 retrieval만 담당).
    bm25_tokens가 비면 BM25 채널을 건너뛰고 d_rows=[]로 인덱스를 고정한다.

    글로벌 세마포어(core.concurrency.vector_global_sema)로 동시 채널 수를 cap한다.
    프로세스 글로벌 ai_session_ctx() 앱 엔진/풀을 사용한다.

    Returns
    -------
    (a_rows, b_rows, c_rows, d_rows) — Track A/B/C/BM25 결과.
    """
    tasks = [
        _run_channel(
            lambda s: _safe_vector_search(
                s,
                query_vector,
                row_kind="identity",
                max_class_name=max_class_name,
                area_name=area_name,
                service_status=service_status,
            )
        ),
        _run_channel(
            lambda s: _safe_vector_search(s, query_vector, row_kind="summary")
        ),
        _run_channel(lambda s: _safe_question_search(s, query_vector)),
    ]
    if bm25_tokens:
        tasks.append(_run_channel(lambda s: _safe_bm25_search(s, bm25_tokens)))

    gathered = await asyncio.gather(*tasks)
    a_rows: list[dict] = gathered[0]
    b_rows: list[dict] = gathered[1]
    c_rows: list[dict] = gathered[2]
    d_rows: list[dict] = gathered[3] if bm25_tokens else []
    return a_rows, b_rows, c_rows, d_rows


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
    ) -> dict:
        """질의 정제 → 임베딩 → 4채널 병렬 검색 → 가중 RRF.

        채널마다 독립 ai_session_ctx()로 세션을 열어 asyncio.gather로 동시 실행한다.
        프로세스 글로벌 세마포어(core.concurrency.vector_global_sema)로 동시 채널 수를 cap한다.

        반환 state 의 vector.results 에는 검색 메타데이터만 채운다:
          [{service_id, rrf_score}, ...]
        원본 데이터 hydration 은 HydrationNode 가 단독으로 담당한다.

        Router가 이미 refined_query와 post-filter를 산출한 경우
        (state["plan"]["refined_query"] 존재), 중복 LLM 호출을 피하기 위해 refine 체인을
        skip하고 state["filters"]의 max_class_name/area_name/service_status 값을
        그대로 사용한다.
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
            # 폴백/강제-재검색 경로(plan.refined_query 없음 — 완화 retry 등). refine-chain 이
            # max_class/area/status 를 재추출해 Track A post-filter 로만 넘긴다(아래 gather).
            # ponytail: 이 경로는 refined.target_audience 를 state["filters"] 로 반영하지
            # 않는다(post-RRF gate 미발동, no-op) — 의도적이다. 대상 필터 적용은 1차 경로
            # (router 가 state["filters"] 채움)가 담당하고, 이 경로는 완화 재검색이라 직전에
            # 드롭됐을 수 있는 대상 제약을 message 에서 되살려 완화를 되돌리면 안 된다.
            refined = await self._refine_chain.ainvoke({"message": state["message"]})

        query_vector = await self._embeddings.aembed_query(refined.refined_query)
        # kiwipiepy.tokenize()는 동기 C 확장 — asyncio.to_thread()로 오프로드.
        tokens = await atokenize_query(refined.refined_query)
        bm25_tokens = [t for t in tokens if t not in _BM25_STOPWORDS]

        # 4채널 병렬 실행 (run_parallel_channels 로 추출).
        # 채널마다 독립 ai_session_ctx()로 세션을 열어 asyncpg 동시 쿼리 제약을 우회한다.
        # bm25_tokens 없으면 채널 D를 태스크에 포함하지 않고 빈 결과로 인덱스를 고정한다.
        #
        # 글로벌 세마포어(core.concurrency.vector_global_sema) — 동시 채널 단일 가드.
        #   on_ai 풀(cap=50)이 고갈되지 않도록 프로세스 전체 동시 채널 수를 제한한다.
        #   단일 인스턴스 200 QPS 기준(config.py 산정): 200 QPS × 4채널 fan-out 잠재 쿼리를
        #   vector_global_concurrency(기본 40)로 캡해 풀(cap 50) 이내로 유지(persist/trace 여유 ~10).
        a_rows, b_rows, c_rows, d_rows = await run_parallel_channels(
            query_vector,
            bm25_tokens,
            max_class_name=refined.max_class_name,
            area_name=refined.area_name,
            service_status=refined.service_status,
        )

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
