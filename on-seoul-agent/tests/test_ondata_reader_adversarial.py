"""B3-1 2차 QA — OnDataReader 게이트웨이 적대 검증.

1차 QA(동작 보존·acquire-use-release·주입 폴백·호환장치 퇴역 안전성)와 겹치지
않는 새로운/적대적 각도만 다룬다:

  1. 동시성/공유 상태: default_reader 무상태성(인스턴스 속성 0) + asyncio.gather
     동시 호출 시 각 호출이 독립 세션을 얻는지(세션이 인스턴스에 캐싱되지 않음)를
     OnDataReader 자체에 직접 찌른다(1차의 graph-level isolation 을 넘어서).
  2. 주입 경계 누락: RetrievalNodes 의 on_data 쓰는 노드가 전부 self._ondata 경유인지
     sabotage(주입 reader 가 호출되지 않으면 RuntimeError) 로 실증.
  3. 퇴역 호환장치 잔존 영향: _hydration property·_correction_phase 제거 후
     모듈 함수 위임이 여전히 default_reader 경유인지.
  4. 에러 경로 전파: session/hydrate/map_proximity 에서 세션 획득 실패·tool 예외가
     기존(모듈 함수 시절)과 동일하게 호출자에게 전파되는지.
  5. import 시점 부작용: default_reader 생성이 무해(DB 연결 미발생)한지.

전부 가짜 OnDataReader 주입 또는 data_session_ctx 패치만 쓰고 실 DB 는 건드리지 않는다.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agents._ondata_gateway as gw
from agents._ondata_gateway import OnDataReader, default_reader
from agents.nodes.retrieval import RetrievalNodes
from schemas.state import IntentType
from tests.helpers import make_agent_state

# asyncio_mode="auto"(pyproject) 이므로 async 테스트는 마커 없이 자동 실행된다.
# 모듈 레벨 마커를 두면 동기 테스트(무상태성/퇴역 검증)에 잘못 붙어 경고가 난다.


# ---------------------------------------------------------------------------
# 공용 — data_session_ctx 를 패치해 OnDataReader 의 세션 수명을 관측하는 헬퍼
# ---------------------------------------------------------------------------


class _SessionTracker:
    """data_session_ctx 대체 — 매 acquire 마다 새 sentinel 세션을 발급하고

    acquire/release 순서를 기록한다. 동일 인스턴스가 세션을 캐싱하지 않는지,
    호출당 acquire-use-release 가 지켜지는지 관측하기 위함.
    """

    def __init__(self) -> None:
        self.acquired: list[object] = []
        self.released: list[object] = []
        self.live: int = 0
        self.max_live: int = 0
        self._seq = 0

    def __call__(self):
        tracker = self

        @asynccontextmanager
        async def _ctx():
            tracker._seq += 1
            sess = f"session-{tracker._seq}"
            tracker.acquired.append(sess)
            tracker.live += 1
            tracker.max_live = max(tracker.max_live, tracker.live)
            try:
                yield sess
            finally:
                tracker.live -= 1
                tracker.released.append(sess)

        return _ctx()


# ===========================================================================
# 각도 1 — 동시성/공유 상태 적대 검증
# ===========================================================================


class TestOnDataReaderStatelessness:
    """OnDataReader 가 정말 무상태인지 코드로 확인 + 동시 호출 독립 세션 실증."""

    def test_reader_has_no_instance_attributes(self):
        """무상태 주장: OnDataReader() 인스턴스에 속성이 0개여야 한다.

        속성이 생기면 프로세스 공유 default_reader 에서 요청 간 교차 오염 위험.
        __slots__ 미정의이므로 __dict__ 가 비어있음을 확인한다.
        """
        reader = OnDataReader()
        assert reader.__dict__ == {}, (
            f"OnDataReader 가 상태를 보유함(공유 안전성 위반): {reader.__dict__}"
        )
        # default_reader(프로세스 공유 인스턴스)도 동일하게 무상태여야 한다.
        assert default_reader.__dict__ == {}, (
            f"default_reader 가 상태를 보유함: {default_reader.__dict__}"
        )

    def test_no_init_defined(self):
        """OnDataReader 는 자체 __init__ 이 없어야 한다(상태 도입 가드).

        누군가 __init__ 에 self.<x>= 를 추가하면 무상태 계약이 깨지므로
        명시적으로 object.__init__ 임을 고정한다.
        """
        assert OnDataReader.__init__ is object.__init__

    async def test_concurrent_session_calls_get_independent_sessions(self):
        """asyncio.gather 로 default_reader.session() 을 동시 호출하면

        각 호출이 서로 다른 세션을 얻어야 한다(인스턴스에 세션 캐싱 없음).
        동시에 살아있는 세션이 호출 수만큼 존재(max_live==N)함을 실증한다.
        """
        import asyncio

        tracker = _SessionTracker()
        N = 8

        async def use_session(reader: OnDataReader, hold: asyncio.Event):
            async with reader.session() as s:
                # 모든 코루틴이 세션을 잡을 때까지 블록 → 동시 보유 강제
                await hold.wait()
                return s

        gate = asyncio.Event()
        with patch.object(gw, "data_session_ctx", tracker):
            tasks = [
                asyncio.create_task(use_session(default_reader, gate)) for _ in range(N)
            ]
            # 모든 task 가 세션을 잡고 gate 에서 대기하도록 양보
            while tracker.live < N:
                await asyncio.sleep(0)
            assert tracker.live == N  # N개 세션 동시 보유
            gate.set()
            sessions = await asyncio.gather(*tasks)

        # 동시 보유 정점이 N == 세션이 인스턴스에 캐싱/직렬화되지 않았음
        assert tracker.max_live == N
        # 각 호출이 고유 세션을 받았다(공유/재사용 없음)
        assert len(set(sessions)) == N
        # 전부 반납됨(누수 없음)
        assert tracker.live == 0
        assert len(tracker.released) == N

    async def test_concurrent_hydrate_each_acquires_own_session(self):
        """동시 hydrate() 호출이 각자 독립 세션으로 tool 을 부른다.

        tool(_hydrate_services)을 패치해 어떤 세션으로 불렸는지 기록하고,
        동시 호출들이 서로 다른 세션을 썼음을 확인한다(인스턴스 세션 공유 부재).
        """
        import asyncio

        tracker = _SessionTracker()
        seen_sessions: list[object] = []

        async def fake_hydrate(session, ids):
            seen_sessions.append(session)
            await asyncio.sleep(0)  # 다른 코루틴에 양보(인터리빙 유도)
            return [{"service_id": i} for i in ids]

        with (
            patch.object(gw, "data_session_ctx", tracker),
            patch.object(gw, "_hydrate_services", fake_hydrate),
        ):
            results = await asyncio.gather(
                *(default_reader.hydrate([f"S{i}"]) for i in range(6))
            )

        assert len(seen_sessions) == 6
        assert len(set(seen_sessions)) == 6, "동시 hydrate 가 세션을 공유함"
        assert all(len(r) == 1 for r in results)
        assert tracker.live == 0  # 전부 반납


# ===========================================================================
# 각도 2 — 주입 경계 누락 탐색 (sabotage 로 self._ondata 미경유 시 폭발)
# ===========================================================================


class _SabotageReader:
    """주입됐는데 호출 안 되면 안전, 호출되면 sentinel 세션을 추적 발급.

    반대로, 노드가 self._ondata 를 안 거치고 모듈 함수/data_session_ctx 로
    새면 이 reader 의 카운터가 0 으로 남아 테스트가 잡아낸다.
    """

    def __init__(self) -> None:
        self.session_calls = 0
        self.map_calls = 0
        self.last_session = None

    @asynccontextmanager
    async def session(self):
        self.session_calls += 1
        sess = MagicMock(name=f"ondata-session-{self.session_calls}")
        self.last_session = sess
        yield sess

    async def map_proximity(self, lat, lng, radius_m):
        self.map_calls += 1
        return {"features": [{"properties": {"service_id": "M1"}}]}


def _retrieval_with(reader, **agents):
    return RetrievalNodes(
        sql=agents.get("sql", MagicMock()),
        vector=agents.get("vector", MagicMock()),
        analytics=agents.get("analytics", MagicMock()),
        hydration=agents.get("hydration", AsyncMock(return_value={})),
        ondata=reader,
    )


class TestInjectionBoundaryNoLeak:
    """on_data 쓰는 노드(sql/map/analytics/hydration)가 전부 self._ondata 경유인지."""

    async def test_sql_node_uses_injected_reader_session(self):
        reader = _SabotageReader()
        sql = MagicMock()
        sql.search = AsyncMock(return_value={"sql": {"results": [], "keyword": "k"}})
        nodes = _retrieval_with(reader, sql=sql)

        await nodes.sql_node(make_agent_state(intent=IntentType.SQL_SEARCH))

        assert reader.session_calls == 1, "sql_node 가 주입 reader 세션을 안 씀(누수)"
        # tool 이 주입 reader 의 세션을 그대로 받았는지(다른 경로 세션 아님)
        sql.search.assert_awaited_once()
        assert sql.search.await_args.args[1] is reader.last_session

    async def test_analytics_node_uses_injected_reader_session(self):
        reader = _SabotageReader()
        analytics = MagicMock()
        analytics.run = AsyncMock(
            return_value={"analytics": {"results": [], "group_by": "g", "metric": "m"}}
        )
        nodes = _retrieval_with(reader, analytics=analytics)

        await nodes.analytics_node(make_agent_state(intent=IntentType.SQL_SEARCH))

        assert reader.session_calls == 1, "analytics_node 가 주입 reader 를 안 씀"
        assert analytics.run.await_args.args[1] is reader.last_session

    async def test_hydration_node_uses_injected_reader_session(self):
        reader = _SabotageReader()
        hydration = AsyncMock(
            return_value={"hydration": {"hydrated_services": [{"service_id": "S1"}]}}
        )
        nodes = _retrieval_with(reader, hydration=hydration)

        await nodes.hydration_node(
            make_agent_state(
                intent=IntentType.SQL_SEARCH, sql_results=[{"service_id": "S1"}]
            )
        )

        assert reader.session_calls == 1, "hydration_node 가 주입 reader 를 안 씀"
        assert hydration.await_args.args[1] is reader.last_session

    async def test_map_node_uses_injected_reader_map_proximity(self):
        reader = _SabotageReader()
        nodes = _retrieval_with(reader)
        state = make_agent_state(intent=IntentType.MAP)
        state["user_lat"] = 37.5
        state["user_lng"] = 127.0

        result = await nodes.map_node(state)

        assert reader.map_calls == 1, "map_node 가 주입 reader.map_proximity 를 안 씀"
        # map_node 는 session() 을 쓰지 않고 map_proximity 만 쓴다(게이트웨이가 세션 캡슐화)
        assert reader.session_calls == 0
        assert "map_node" in result["node_path"]

    async def test_vector_node_does_not_touch_ondata_reader(self):
        """vector_node 는 VectorAgent 자체 ai_session 을 쓰므로 on_data reader 미사용.

        주입 reader 가 단 한 번도 호출되지 않아야 한다(올바른 경계 — on_data 아님).
        """
        reader = _SabotageReader()
        vector = MagicMock()
        vector.search = AsyncMock(
            return_value={"vector": {"results": []}, "plan": {"refined_query": "q"}}
        )
        nodes = _retrieval_with(reader, vector=vector)

        await nodes.vector_node(make_agent_state(intent=IntentType.VECTOR_SEARCH))

        assert reader.session_calls == 0, "vector_node 가 on_data reader 를 잘못 사용함"
        assert reader.map_calls == 0
        vector.search.assert_awaited_once()
        # vector.search 는 세션 인자를 받지 않는다(내부에서 ai_session 자체 획득)
        assert len(vector.search.await_args.args) == 1


# ===========================================================================
# 각도 3 — 퇴역 호환장치(_hydration property·_correction_phase) 잔존 영향
# ===========================================================================


class TestRetiredShimsGone:
    """제거된 호환장치가 코드 표면에서 사라졌고 우회가 없는지 직접 확인."""

    def test_graphnodes_hydration_is_plain_attribute_not_property(self):
        """GraphNodes._hydration 이 전파 property 가 아니라 평범한 속성이어야 한다."""
        from agents.nodes.graph_nodes import GraphNodes

        assert not isinstance(
            GraphNodes.__dict__.get("_hydration"), property
        ), "_hydration 전파 property 가 아직 살아있음(퇴역 누락)"

    def test_graphnodes_has_no_correction_phase_builder(self):
        """_correction_phase 지연 빌더가 제거됐어야 한다."""
        from agents.nodes.graph_nodes import GraphNodes

        assert not hasattr(GraphNodes, "_correction_phase"), (
            "_correction_phase 지연 빌더가 아직 살아있음(퇴역 누락)"
        )

    def test_redis_property_still_propagates(self):
        """대조군: _redis 전파 property 는 의도적으로 유지(부분 퇴역)됐는지 확인."""
        from agents.nodes.graph_nodes import GraphNodes

        assert isinstance(GraphNodes.__dict__.get("_redis"), property)


# ===========================================================================
# 각도 3 보강 — 모듈 함수 위임이 여전히 default_reader 경유인지
# ===========================================================================


class TestModuleFunctionDelegation:
    """B2 호환 모듈 함수(hydrate/session/map_proximity)가 default_reader 위임 유지."""

    async def test_module_hydrate_delegates_to_default_reader(self):
        """gw.hydrate(ids) 가 default_reader.hydrate 로 위임되는지(Reference 경로)."""
        with patch.object(
            default_reader, "hydrate", AsyncMock(return_value=[{"service_id": "X"}])
        ) as m:
            out = await gw.hydrate(["X"])
        m.assert_awaited_once_with(["X"])
        assert out == [{"service_id": "X"}]

    async def test_module_map_proximity_delegates_to_default_reader(self):
        with patch.object(
            default_reader, "map_proximity", AsyncMock(return_value={"features": []})
        ) as m:
            out = await gw.map_proximity(1.0, 2.0, 500)
        m.assert_awaited_once_with(1.0, 2.0, 500)
        assert out == {"features": []}

    async def test_module_session_delegates_to_default_reader(self):
        tracker = _SessionTracker()
        with patch.object(gw, "data_session_ctx", tracker):
            async with gw.session() as s:
                assert s == "session-1"
        assert tracker.live == 0  # 위임 경로도 acquire-use-release


# ===========================================================================
# 각도 4 — 에러 경로/예외 전파 동일성
# ===========================================================================


class TestErrorPropagation:
    """세션 획득 실패·tool 예외가 OnDataReader 경유로도 그대로 전파되는지."""

    async def test_session_acquire_failure_propagates(self):
        """data_session_ctx() 진입에서 예외가 나면 reader.session() 진입에서 전파."""

        @asynccontextmanager
        async def failing_ctx():
            raise RuntimeError("pool 고갈")
            yield  # pragma: no cover

        reader = OnDataReader()
        with patch.object(gw, "data_session_ctx", lambda: failing_ctx()):
            with pytest.raises(RuntimeError, match="pool 고갈"):
                async with reader.session():
                    pass

    async def test_hydrate_tool_exception_propagates(self):
        """_hydrate_services 예외가 reader.hydrate() 호출자에게 그대로 올라온다."""
        tracker = _SessionTracker()
        reader = OnDataReader()
        with (
            patch.object(gw, "data_session_ctx", tracker),
            patch.object(
                gw, "_hydrate_services", AsyncMock(side_effect=ValueError("쿼리 실패"))
            ),
        ):
            with pytest.raises(ValueError, match="쿼리 실패"):
                await reader.hydrate(["S1"])
        # 예외 발생에도 세션은 정상 반납돼야 한다(누수 없음)
        assert tracker.live == 0
        assert len(tracker.released) == 1

    async def test_map_proximity_tool_exception_propagates_and_releases(self):
        tracker = _SessionTracker()
        reader = OnDataReader()
        with (
            patch.object(gw, "data_session_ctx", tracker),
            patch.object(
                gw, "_map_search", AsyncMock(side_effect=RuntimeError("earthdistance 오류"))
            ),
        ):
            with pytest.raises(RuntimeError, match="earthdistance 오류"):
                await reader.map_proximity(1.0, 2.0, 500)
        assert tracker.live == 0  # 예외에도 반납

    async def test_hydration_node_swallows_reader_session_failure(self):
        """노드 레벨: 주입 reader.session() 이 터져도 hydration_node 는 [] fallback.

        게이트웨이 경유로도 try/except 동작이 모듈함수 시절과 동일(예외 삼킴→[])한지.
        """

        class _BoomReader:
            @asynccontextmanager
            async def session(self):
                raise RuntimeError("세션 폭발")
                yield  # pragma: no cover

        nodes = _retrieval_with(_BoomReader())
        result = await nodes.hydration_node(
            make_agent_state(
                intent=IntentType.SQL_SEARCH, sql_results=[{"service_id": "S1"}]
            )
        )
        assert result["hydration"]["hydrated_services"] == []
        assert "hydration_error" in result["node_path"]

    async def test_map_node_swallows_reader_failure(self):
        """map_node: 주입 reader.map_proximity 예외 시 error + map_error node_path."""

        class _BoomReader:
            @asynccontextmanager
            async def session(self):
                yield MagicMock()

            async def map_proximity(self, lat, lng, radius_m):
                raise RuntimeError("좌표 검색 폭발")

        nodes = _retrieval_with(_BoomReader())
        state = make_agent_state(intent=IntentType.MAP)
        state["user_lat"] = 37.5
        state["user_lng"] = 127.0
        result = await nodes.map_node(state)
        assert result["error"] == "좌표 검색 폭발"
        assert "map_error" in result["node_path"]


# ===========================================================================
# 각도 5 — default_reader import 시점 부작용 부재
# ===========================================================================


class TestImportSideEffects:
    """모듈 로드 시 default_reader 생성이 무해(DB 연결 미발생)한지."""

    def test_default_reader_is_bare_instance(self):
        """default_reader 가 단순 객체(속성 0, 클래스 일치)인지."""
        assert isinstance(default_reader, OnDataReader)
        assert default_reader.__dict__ == {}

    def test_constructing_reader_does_not_open_session(self):
        """OnDataReader() 생성 자체가 data_session_ctx 를 부르지 않는다.

        생성만으로 세션/연결이 열리면 import 시점 부작용 위험.
        """
        tracker = _SessionTracker()
        with patch.object(gw, "data_session_ctx", tracker):
            _ = OnDataReader()
        assert tracker.acquired == [], "생성만으로 세션이 열림(import 부작용 위험)"

    def test_gateway_module_reimport_is_idempotent(self):
        """게이트웨이 모듈 재import 가 부작용 없이 동일 클래스를 노출하는지."""
        import importlib

        mod = importlib.import_module("agents._ondata_gateway")
        assert mod.OnDataReader is OnDataReader
        assert "OnDataReader" in mod.__all__
        assert "default_reader" in mod.__all__
