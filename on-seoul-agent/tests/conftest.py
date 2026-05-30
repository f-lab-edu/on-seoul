import gc
import os
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

# .env를 먼저 로드해서 실제 API 키가 있으면 os.environ에 반영한다.
# setdefault는 키가 없을 때만 설정하므로 실제 키를 덮어쓰지 않는다.
load_dotenv()

# 단위 테스트 폴백 — 실제 DB 없이도 Settings() 초기화가 통과되도록
os.environ.setdefault(
    "ON_AI_DATABASE_URL", "postgresql+asyncpg://test:test@localhost/on_ai"
)
os.environ.setdefault(
    "ON_DATA_DATABASE_URL", "postgresql+asyncpg://test:test@localhost/on_data"
)


@pytest.fixture()
def mock_graph():
    """routers.chat._resolve_graph를 MagicMock으로 대체한다.

    테스트에서 `mock_graph` fixture를 인자로 선언하면 AgentGraph 없이
    mock_graph.stream 에 원하는 generator를 할당해 사용할 수 있다.
    """
    graph = MagicMock()
    with patch("routers.chat._resolve_graph", return_value=graph):
        yield graph


@pytest.fixture(autouse=True)
def _force_gc_after_test():
    """테스트 종료 후 순환 참조 GC를 강제 실행한다.

    LangGraph CompiledGraph ↔ AgentGraph bound method 간 순환 참조는
    Python 참조 카운팅으로 해제되지 않는다. 테스트마다 AgentGraph를 생성하는
    test_graph.py 등에서 누적 시 OOM이 발생하므로 사이클 GC를 명시적으로 호출한다.
    """
    yield
    gc.collect()
