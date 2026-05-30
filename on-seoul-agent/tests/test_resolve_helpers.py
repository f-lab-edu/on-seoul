"""routers.chat._resolve_redis / _resolve_graph 헬퍼 단위 테스트.

커버 대상:
- _resolve_redis: app.state.redis가 None일 때 get_redis() fallback 경로 (line 62-65)
- _resolve_graph: app.state.graph가 None일 때 AgentGraph() fallback 경로 (line 70-73)
- _resolve_graph: app.state.graph가 설정되어 있으면 그것을 반환하는 happy-path
- _resolve_redis: app.state.redis가 설정되어 있으면 그것을 반환하는 happy-path
- app.state 자체가 없을 때 AttributeError 없이 fallback 동작
- mock_graph fixture와 _mock_redis_io autouse fixture 간 충돌 없음
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request

from routers.chat import _resolve_graph, _resolve_redis


# ---------------------------------------------------------------------------
# 헬퍼: Request 객체 생성
# ---------------------------------------------------------------------------


def _make_request(app: FastAPI) -> Request:
    """TestClient scope를 이용해 실제 FastAPI Request 객체를 만든다."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/chat/stream",
        "query_string": b"",
        "headers": [],
        "app": app,
    }
    return Request(scope)


# ---------------------------------------------------------------------------
# _resolve_redis 테스트
# ---------------------------------------------------------------------------


class TestResolveRedis:
    def test_returns_state_redis_when_set(self):
        """app.state.redis가 있으면 get_redis() 호출 없이 그것을 반환한다."""
        app = FastAPI()
        sentinel = MagicMock(name="redis_sentinel")
        app.state.redis = sentinel

        request = _make_request(app)
        with patch("routers.chat.get_redis") as mock_get_redis:
            result = _resolve_redis(request)

        assert result is sentinel
        mock_get_redis.assert_not_called()

    def test_fallback_to_get_redis_when_state_redis_is_none(self):
        """app.state.redis가 None이면 get_redis()를 호출해 fallback Redis를 반환한다."""
        app = FastAPI()
        app.state.redis = None  # 명시적 None

        fallback_redis = MagicMock(name="fallback_redis")
        request = _make_request(app)
        with patch(
            "routers.chat.get_redis", return_value=fallback_redis
        ) as mock_get_redis:
            result = _resolve_redis(request)

        assert result is fallback_redis
        mock_get_redis.assert_called_once()

    def test_fallback_to_get_redis_when_state_has_no_redis_attr(self):
        """app.state에 redis 속성이 없으면 get_redis() fallback 경로를 탄다.

        lifespan 없이 생성된 app(테스트 환경)에서 app.state.redis를 설정하지 않은 케이스.
        getattr(app.state, 'redis', None)이 None을 반환하므로 fallback이 작동해야 한다.
        """
        app = FastAPI()
        # redis 속성을 설정하지 않음 — lifespan 미실행 상황 재현

        fallback_redis = MagicMock(name="fallback_redis")
        request = _make_request(app)
        with patch(
            "routers.chat.get_redis", return_value=fallback_redis
        ) as mock_get_redis:
            result = _resolve_redis(request)

        assert result is fallback_redis
        mock_get_redis.assert_called_once()


# ---------------------------------------------------------------------------
# _resolve_graph 테스트
# ---------------------------------------------------------------------------


class TestResolveGraph:
    def test_returns_state_graph_when_set(self):
        """app.state.graph가 있으면 AgentGraph 생성 없이 그것을 반환한다."""
        app = FastAPI()
        sentinel_graph = MagicMock(name="graph_sentinel")
        app.state.graph = sentinel_graph
        app.state.redis = MagicMock(name="redis_sentinel")

        request = _make_request(app)
        with patch("routers.chat.AgentGraph") as mock_agent_graph:
            result = _resolve_graph(request)

        assert result is sentinel_graph
        mock_agent_graph.assert_not_called()

    def test_fallback_creates_agent_graph_when_state_graph_is_none(self):
        """app.state.graph가 None이면 AgentGraph(redis=...)를 새로 생성한다."""
        app = FastAPI()
        app.state.graph = None  # 명시적 None
        redis_mock = MagicMock(name="redis_mock")
        app.state.redis = redis_mock

        new_graph = MagicMock(name="new_graph")
        request = _make_request(app)
        with patch(
            "routers.chat.AgentGraph", return_value=new_graph
        ) as mock_agent_graph:
            result = _resolve_graph(request)

        assert result is new_graph
        mock_agent_graph.assert_called_once_with(redis=redis_mock)

    def test_fallback_creates_agent_graph_when_state_has_no_graph_attr(self):
        """app.state에 graph 속성이 없으면 AgentGraph fallback을 생성한다.

        lifespan 없이 생성된 app에서 app.state.graph가 설정되지 않은 케이스.
        """
        app = FastAPI()
        # graph 속성 미설정 — lifespan 미실행 상황 재현
        app.state.redis = MagicMock(name="redis_mock")

        new_graph = MagicMock(name="new_graph")
        request = _make_request(app)
        with patch(
            "routers.chat.AgentGraph", return_value=new_graph
        ) as mock_agent_graph:
            result = _resolve_graph(request)

        assert result is new_graph
        mock_agent_graph.assert_called_once()

    def test_fallback_uses_resolve_redis_to_get_redis(self):
        """_resolve_graph fallback은 _resolve_redis를 통해 redis를 조달한다.

        app.state.graph가 None이고 app.state.redis도 없을 때,
        _resolve_redis의 get_redis() fallback까지 연쇄적으로 동작해야 한다.
        """
        app = FastAPI()
        # graph, redis 모두 설정 안 함

        fallback_redis = MagicMock(name="fallback_redis")
        new_graph = MagicMock(name="new_graph")
        request = _make_request(app)

        with (
            patch("routers.chat.get_redis", return_value=fallback_redis),
            patch(
                "routers.chat.AgentGraph", return_value=new_graph
            ) as mock_agent_graph,
        ):
            result = _resolve_graph(request)

        assert result is new_graph
        # AgentGraph는 fallback_redis로 생성되어야 한다
        mock_agent_graph.assert_called_once_with(redis=fallback_redis)


# ---------------------------------------------------------------------------
# mock_graph fixture와 _mock_redis_io autouse 충돌 검증
#
# test_chat_router.py의 _mock_redis_io는 routers.chat._resolve_redis를 patch한다.
# conftest.py의 mock_graph는 routers.chat._resolve_graph를 patch한다.
# 두 fixture가 동시에 적용될 때 서로를 덮어쓰지 않는지 확인한다.
# ---------------------------------------------------------------------------


class TestFixtureCompatibility:
    @pytest.fixture()
    def _local_mock_redis_io(self):
        """test_chat_router.py의 _mock_redis_io와 동일한 패치 셋."""
        from unittest.mock import AsyncMock

        with (
            patch("routers.chat.get_recent_queries", new=AsyncMock(return_value=[])),
            patch("routers.chat.push_recent_query", new=AsyncMock(return_value=None)),
            patch("routers.chat._resolve_redis", return_value=MagicMock()),
        ):
            yield

    def test_mock_graph_and_mock_redis_io_do_not_conflict(
        self, mock_graph, _local_mock_redis_io
    ):
        """mock_graph fixture와 _mock_redis_io autouse fixture가 동시에 적용돼도
        각자의 대상(graph/redis)을 독립적으로 패치한다.

        두 fixture는 서로 다른 심볼을 패치하므로 충돌하지 않는다.
        - mock_graph: routers.chat._resolve_graph
        - _mock_redis_io: routers.chat._resolve_redis, get_recent_queries, push_recent_query
        """
        # mock_graph fixture가 올바른 MagicMock을 반환하는지 확인
        assert mock_graph is not None
        assert isinstance(mock_graph, MagicMock)

        # _resolve_graph가 패치되어 mock_graph를 반환함을 확인
        app = FastAPI()
        request = _make_request(app)
        # _resolve_graph는 이미 patch("routers.chat._resolve_graph", return_value=mock_graph)로 덮여있다
        # mock_graph fixture가 _resolve_graph 자체를 교체했으므로
        # 모듈 내 _resolve_graph 심볼을 직접 호출하면 mock이 반환된다
        import routers.chat as chat_module  # noqa: PLC0415

        assert chat_module._resolve_graph(request) is mock_graph
