"""GraphNodes — composition root + 위임 facade.

AgentGraph에서 노드/엣지 로직 책임을 분리한다.
노드 구현은 페이즈 클래스가, 그래프 조립과 실행은 AgentGraph가 담당한다.

변경 이유:
    AgentGraph가 그래프 조립, 노드 구현, 엣지 로직, 런타임 상태 관리,
    공개 실행 인터페이스라는 5가지 책임을 가졌다.
    GraphNodes 분리로 각 클래스의 변경 이유(reason to change)를 단일화한다.

세션·타이밍:
    GraphNodes 인스턴스는 AgentGraph가 소유하며 프로세스 내 싱글톤으로 공유된다.
    세션은 각 노드 메서드 안에서 *_session_ctx()로 acquire-use-release(노드 로컬).
    실행 상태(node_path, started_at)는 AgentState 슬롯으로 per-request 격리된다.
    (prepare()는 제거됐고, 대응 로직은 graph._prepare_state()와 AgentState reducer로 이동)

캐시 노드만 클래스로 분리된 이유:
    CacheCheckNode / CacheWriteNode는 Redis 의존성을 명시적으로 주입받고
    단위 테스트에서 격리성을 확보하기 위해 별도 호출 가능 객체로 분리한다.
    다른 노드는 RouterAgent/SqlAgent/VectorAgent/AnswerAgent에 위임하므로
    페이즈 클래스 메서드로 충분하다.
"""

from typing import Any

from agents._ondata_gateway import OnDataReader, default_reader
from agents.analytics_agent import AnalyticsAgent
from agents.answer_agent import AnswerAgent
from agents.hydration_node import HydrationNode
from agents.nodes.answer import AnswerNodes
from agents.nodes.cache_nodes import CacheCheckNode, CacheWriteNode
from agents.nodes.correction import CorrectionNodes
from agents.nodes.observability import ObservabilityNodes
from agents.nodes.planning import PlanningNodes
from agents.nodes.reference import ReferenceNodes
from agents.nodes.retrieval import RetrievalNodes
from agents.router_agent import RouterAgent
from agents.sql_agent import SqlAgent
from agents.triage_agent import TriageAgent
from agents.vector_agent import VectorAgent
from schemas.state import AgentState


class GraphNodes:
    """AgentGraph 노드·엣지 facade (composition root).

    주입 의존(에이전트·redis·hydration)과 페이즈 인스턴스만 보유하며 요청별 가변
    상태는 두지 않는다. 인스턴스는 AgentGraph.__init__()에서 1회 생성되어 프로세스
    내에서 공유된다.
    제안 0(요청 격리): 요청별 가변 자원/상태를 인스턴스 속성으로 두지 않는다.
      - node_path → AgentState 슬롯 (node_path_reducer 로 per-invoke 누적).
      - 시작 시각 → AgentState["started_at"].
    따라서 동시 요청이 같은 GraphNodes 를 공유해도 세션/경로 교차가 발생하지 않는다.

    구조(C2): god-class 를 6개 페이즈 클래스(Reference/Planning/Retrieval/Answer/
    Correction/Observability)로 분해하고, GraphNodes 는 각 페이즈 인스턴스를 보유한
    composition root + 위임 facade 로 남는다. graph.py 가 등록하는 노드명·테스트가
    직접 호출하는 메서드명을 위임 메서드로 그대로 노출해 외부 표면을 보존한다.

    제안 0-6(노드 로컬 세션): DB 를 쓰는 노드는 노드 내부에서 `data_session_ctx()`/
    `ai_session_ctx()` 로 풀에서 세션을 잡고 즉시 반납한다(acquire-use-release).
    세션은 노드 메서드 지역 변수로만 존재하므로 인스턴스 속성 교차도 원천 차단된다.
    """

    def __init__(
        self,
        router: RouterAgent | TriageAgent | None = None,
        sql_agent: SqlAgent | None = None,
        vector_agent: VectorAgent | None = None,
        answer_agent: AnswerAgent | None = None,
        analytics_agent: AnalyticsAgent | None = None,
        redis: Any = None,
        hydration: HydrationNode | None = None,
        triage: TriageAgent | None = None,
        ondata: OnDataReader | None = None,
    ) -> None:
        # triage 우선, router는 하위호환 별칭
        self._triage = triage or (router if isinstance(router, TriageAgent) else None)
        self._router = router if isinstance(router, RouterAgent) else None
        self._sql = sql_agent or SqlAgent()
        self._vector = vector_agent or VectorAgent()
        self._answer = answer_agent or AnswerAgent()
        self._analytics = analytics_agent or AnalyticsAgent()
        # _redis 는 평범한 인스턴스 속성 — 생성자 시점 1회 주입으로 각 페이즈에 전달한다
        # (B3-2 에서 전파 property 를 퇴역). 사후 변이(nodes._redis=) 전파 계약은 더 이상
        # 없으며, redis 의존이 필요한 테스트는 해당 페이즈(CacheCheckNode/CorrectionNodes)를
        # redis 주입으로 직접 생성하거나 GraphNodes(redis=...)로 주입한다.
        self._redis = redis  # refine 캐시(router_node) 공유 — answer 캐시 노드와 동일 클라이언트
        self._hydration = hydration or HydrationNode()
        # on_data 읽기 게이트웨이(B3-1/B3-2): Retrieval/Reference 에 주입. 기본 default_reader 라
        # 프로덕션 동작 불변. 테스트가 가짜 reader 를 GraphNodes(ondata=...)로 주입 가능.
        self._ondata = ondata or default_reader
        self._cache_check = CacheCheckNode(redis=redis)
        self._cache_write = CacheWriteNode(redis=redis)

        # 페이즈 인스턴스 — 각 페이즈는 자신이 쓰는 의존만 받는다(설계 기준 ④).
        # Reference/Retrieval 은 동일 default_reader(self._ondata)를 공유한다(B3-2).
        self._reference = ReferenceNodes(answer=self._answer, ondata=self._ondata)
        self._planning = PlanningNodes(
            triage=self._triage, router=self._router, redis=self._redis
        )
        self._retrieval = RetrievalNodes(
            sql=self._sql,
            vector=self._vector,
            analytics=self._analytics,
            hydration=self._hydration,
            ondata=self._ondata,
        )
        self._answer_nodes = AnswerNodes(answer=self._answer)
        self._correction = CorrectionNodes(redis=self._redis)
        self._observability = ObservabilityNodes()

    # ------------------------------------------------------------------
    # Reference 페이즈 위임
    # ------------------------------------------------------------------

    async def reference_resolution_node(self, state: AgentState) -> dict[str, Any]:
        return await self._reference.reference_resolution_node(state)

    async def rehydrate_node(self, state: AgentState) -> dict[str, Any]:
        return await self._reference.rehydrate_node(state)

    async def describe_node(self, state: AgentState) -> dict[str, Any]:
        return await self._reference.describe_node(state)

    def route_after_reference(self, state: AgentState) -> str:
        return self._reference.route_after_reference(state)

    # ------------------------------------------------------------------
    # Planning 페이즈 위임
    # ------------------------------------------------------------------

    async def triage_node(self, state: AgentState) -> dict[str, Any]:
        return await self._planning.triage_node(state)

    async def router_node(self, state: AgentState) -> dict[str, Any]:
        return await self._planning.router_node(state)

    def route_by_action(self, state: AgentState) -> str:
        return self._planning.route_by_action(state)

    def route_by_action_fanout(self, state: AgentState) -> list[str] | str:
        return self._planning.route_by_action_fanout(state)

    def post_cache_check(self, state: AgentState) -> str:
        return self._planning.post_cache_check(state)

    def route_by_intent(self, state: AgentState) -> str:
        return self._planning.route_by_intent(state)

    # ------------------------------------------------------------------
    # Retrieval 페이즈 위임
    # ------------------------------------------------------------------

    async def sql_node(self, state: AgentState) -> dict[str, Any]:
        return await self._retrieval.sql_node(state)

    async def vector_node(self, state: AgentState) -> dict[str, Any]:
        return await self._retrieval.vector_node(state)

    async def map_node(self, state: AgentState) -> dict[str, Any]:
        return await self._retrieval.map_node(state)

    async def analytics_node(self, state: AgentState) -> dict[str, Any]:
        return await self._retrieval.analytics_node(state)

    async def hydration_node(self, state: AgentState) -> dict[str, Any]:
        return await self._retrieval.hydration_node(state)

    async def rrf_fusion_node(self, state: AgentState) -> dict[str, Any]:
        return await self._retrieval.rrf_fusion_node(state)

    async def pre_answer_gate_node(self, state: AgentState) -> dict[str, Any]:
        return await self._retrieval.pre_answer_gate_node(state)

    def route_pre_answer_gate(self, state: AgentState) -> str:
        return self._retrieval.route_pre_answer_gate(state)

    # ------------------------------------------------------------------
    # Answer 페이즈 위임
    # ------------------------------------------------------------------

    async def answer_node(self, state: AgentState) -> dict[str, Any]:
        return await self._answer_nodes.answer_node(state)

    async def direct_answer_node(self, state: AgentState) -> dict[str, Any]:
        return await self._answer_nodes.direct_answer_node(state)

    async def ambiguous_node(self, state: AgentState) -> dict[str, Any]:
        return await self._answer_nodes.ambiguous_node(state)

    async def out_of_scope_node(self, state: AgentState) -> dict[str, Any]:
        return await self._answer_nodes.out_of_scope_node(state)

    async def explain_node(self, state: AgentState) -> dict[str, Any]:
        return await self._answer_nodes.explain_node(state)

    # ------------------------------------------------------------------
    # Correction 페이즈 위임
    # ------------------------------------------------------------------

    async def retry_prep_node(self, state: AgentState) -> dict[str, Any]:
        return await self._correction.retry_prep_node(state)

    def self_correction_edge(self, state: AgentState) -> str:
        return self._correction.self_correction_edge(state)

    # ------------------------------------------------------------------
    # Observability 페이즈 위임
    # ------------------------------------------------------------------

    async def search_persist_node(self, state: AgentState) -> dict[str, Any]:
        return await self._observability.search_persist_node(state)

    async def trace_node(self, state: AgentState) -> dict[str, Any]:
        return await self._observability.trace_node(state)

    # ------------------------------------------------------------------
    # Cache 노드 — node_path 부여 래퍼 (facade 보유 유지)
    # ------------------------------------------------------------------

    async def cache_check_node(self, state: AgentState) -> dict[str, Any]:
        """router 직후 cache 조회 — hit 시 state 복원, cache_hit 플래그 설정."""
        result = await self._cache_check(state)
        if result.get("cache_hit"):
            result["node_path"] = ["cache_check_hit"]
        else:
            result["node_path"] = ["cache_check_miss"]
        return result

    async def cache_write_node(self, state: AgentState) -> dict[str, Any]:
        """answer 직후 정상 결과만 캐싱 (skip 조건은 노드 내부 처리)."""
        result = await self._cache_write(state)
        result["node_path"] = ["cache_write"]
        return result
