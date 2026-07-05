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

from collections import Counter
from typing import Any

from langgraph.config import get_stream_writer

# 결과 품질 자각 패스 휴리스틱 임계 — agents/answer_agent.py 와 짝.
_SKEW_MIN_COUNT = 3  # N<3 이면 쏠림 평가 안 함(1~2건 오판 방지).
_SKEW_RATIO_THRESHOLD = 0.8  # most_common(1) 비율 >= 이 값이면 쏠림.
_THIN_MAX_COUNT = 2  # 결과 <=2건이면 빈약(thin).

# 직전 assistant 발화에 통합회원 안내가 나갔는지 근사하는 핵심 문구(저비용·결정적).
# _CLAUSE_RESERVATION_GUIDE 본문에서 가장 식별력 높은 토막을 신호로 본다.
_RESERVATION_GUIDE_MARKER = "통합회원 가입"


def assess_result_quality(
    rows: list[dict[str, Any]],
    *,
    area_filter: str | None,
) -> dict[str, Any] | None:
    """결과 품질 자각 패스 — hydration 결과의 성격(쏠림·빈약)을 경량 점검한다.

    결정적·무비용 휴리스틱(LLM 미호출). 재검색은 하지 않으며 answer 가 소비할
    플래그(result_quality)만 산출한다. 점검할 게 없으면 None(현행 조립 그대로).

    쏠림(skew): N>=3 일 때만, Counter(area_name).most_common(1) 비율이 임계 이상.
      사용자가 지역을 명시(area_filter 채워짐)했으면 정상이므로 억제(사례 161은
      지역 *미지정* 이라 대상).
    빈약(thin): 결과가 1~2건. 0건은 자각 대상 아님(0건 게이트가 처리) → None.

    Args:
        rows: hydration 이 채운 원본 결과 목록(area_name 키 접근).
        area_filter: 사용자가 해소·지정한 자치구(state["filters"]["area_name"]).
            채워져 있으면 쏠림을 억제한다.

    Returns:
        {"skew_field", "skew_value", "skew_ratio", "thin"} 또는 점검할 게 없으면 None.
    """
    n = len(rows)
    if n == 0:
        return None

    thin = n <= _THIN_MAX_COUNT

    skew_field: str | None = None
    skew_value: str | None = None
    skew_ratio: float | None = None
    if n >= _SKEW_MIN_COUNT and not area_filter:
        areas = [r.get("area_name") for r in rows if r.get("area_name")]
        if areas:
            value, count = Counter(areas).most_common(1)[0]
            ratio = count / n
            if ratio >= _SKEW_RATIO_THRESHOLD:
                skew_field = "area_name"
                skew_value = value
                skew_ratio = ratio

    if not thin and skew_field is None:
        return None
    return {
        "skew_field": skew_field,
        "skew_value": skew_value,
        "skew_ratio": skew_ratio,
        "thin": thin,
    }


def reservation_guide_already_shown(history: list[dict[str, str]] | None) -> bool:
    """직전 assistant 발화에 통합회원 안내가 이미 나갔는지 근사한다(상류 history 파싱).

    answer 는 raw history 를 뒤지지 않고 이 bool 만 소비한다(책임 경계).
    보수적으로 직전 assistant 발화의 핵심 문구 일부 일치만 신호로 본다(오탐 시
    안내 *생략* 방향이라 사용자 피해 작음 — 정보 1회 누락 < 매턴 반복).
    """
    if not history:
        return False
    for turn in reversed(history):
        if turn.get("role") == "assistant":
            return _RESERVATION_GUIDE_MARKER in (turn.get("content") or "")
    return False

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


def emit_critic_decision(decision: str, round_index: int, user_rationale: str) -> None:
    """retrieval-critic 라운드 결정 이벤트를 custom stream 으로 흘려보낸다 (L1 Phase 5).

    triage `decision`(단일 1회)과 별개 `_evt` 타입("critic_decision")으로 흘려보내
    두 이벤트가 같은 프레임에서 서로 덮어쓰지 않게 한다. critic 은 라운드마다 1회
    호출되므로 round_index(0-base)로 라운드를 구분한다. 컨텍스트 밖이면 no-op.
    """
    writer = _writer()
    if writer is None:
        return
    writer(
        {
            "_evt": "critic_decision",
            "decision": decision,
            "round": round_index,
            "user_rationale": user_rationale,
        }
    )
