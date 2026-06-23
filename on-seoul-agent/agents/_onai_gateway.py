"""on_ai 쓰기 게이트웨이 — ai_session_ctx 세션 수명 캡슐화(B2-1).

자원=on_ai 쓰기(관측 적재: chat_search_queries/chat_search_results/chat_agent_traces).
게이트웨이는 "세션 수명·자원 선택"만 책임진다. INSERT SQL·페이로드 조립은
ObservabilityNodes 책임으로 둔다(제약 #6 — tool/노드와 책임 분리).

노드 로컬 세션(제약 #2): session() 은 호출당 1회 acquire-use-release.
search_persist_node 와 trace_node 는 각각 독립 session() 을 열어 한 노드의
INSERT/실패가 다른 노드 세션을 오염시키지 않는다(best-effort 격리).

테스트 patch 타깃: agents._onai_gateway.session / agents._onai_gateway.ai_session_ctx.
"""

from contextlib import asynccontextmanager

from core.database import ai_session_ctx


@asynccontextmanager
async def session():
    """관측 적재용 on_ai 세션 1회 (acquire-use-release).

    호출자가 with 블록 안에서 INSERT·commit 후 블록 종료 시 즉시 반납한다.
    """
    async with ai_session_ctx() as s:
        yield s


__all__ = ["session"]
