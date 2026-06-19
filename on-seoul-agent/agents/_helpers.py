"""GraphNodes 보조 헬퍼 — SSE 이벤트 노드-내부 emit (작업 3).

LangGraph 정석: 노드가 자기 진행/결정 이벤트를 custom stream writer 로 직접
흘려보낸다. stream() 의 "어느 단계인지 역추론" if/elif 트리를 제거하기 위한 토대다.

emit_event() 는 LangGraph 노드 컨텍스트 밖(run()/단위 테스트)에서 호출되면 no-op
이다. get_stream_writer() 는 runnable 컨텍스트가 없으면 RuntimeError 를 던지므로
이를 잡아 무시한다. ainvoke(run) 경로는 컨텍스트가 있으나 stream_writer 가
no-op 기본값이라 emit 이 무해하게 흡수된다.

페이로드 규약(stream() 의 "custom" 분기가 이 _evt 로 분기):
  {"_evt": "progress", "step": str, "message": str}
  {"_evt": "decision", "action": str, "routes": list[str], "user_rationale": str}
"""

from typing import Any

from langgraph.config import get_stream_writer

# progress step 문자열·메시지는 기존 stream() 과 1:1 동일해야 한다.
_PROGRESS_MESSAGES: dict[str, str] = {
    "routing": "질문을 분석하고 있습니다...",
    "searching": "관련 정보를 검색하고 있습니다...",
    "answering": "답변을 생성하고 있습니다...",
    "re_searching": "다른 방식으로 다시 검색하고 있습니다...",
}


def _writer() -> Any:
    """현재 노드의 stream writer 를 반환한다. 컨텍스트 밖이면 None.

    get_stream_writer() 는 runnable 컨텍스트가 없으면 RuntimeError 를 던진다
    (run()/ainvoke 는 컨텍스트가 있고 no-op writer 를 반환하므로 안전, 단위
    테스트의 직접 노드 호출만 RuntimeError → None 으로 흡수).
    """
    try:
        return get_stream_writer()
    except (RuntimeError, LookupError):
        return None


def emit_progress(step: str) -> None:
    """progress 이벤트를 custom stream 으로 흘려보낸다(컨텍스트 밖이면 no-op)."""
    writer = _writer()
    if writer is None:
        return
    writer(
        {
            "_evt": "progress",
            "step": step,
            "message": _PROGRESS_MESSAGES[step],
        }
    )


def emit_decision(action: str, routes: list[str], user_rationale: str) -> None:
    """decision 이벤트를 custom stream 으로 흘려보낸다(컨텍스트 밖이면 no-op)."""
    writer = _writer()
    if writer is None:
        return
    writer(
        {
            "_evt": "decision",
            "action": action,
            "routes": routes,
            "user_rationale": user_rationale,
        }
    )
