"""Langfuse LLM 관측가능성 초기화 (Langfuse Cloud, LangChain CallbackHandler).

설계 원칙 (telemetry.py 구조 모방, 별개 파이프라인으로 공존)
- LLM I/O·토큰·비용만: 그래프 실행 경로의 중첩 LCEL 체인을 LangChain 콜백으로 관측.
  인프라 계측(FastAPI/httpx/SQLAlchemy/redis)은 core/telemetry.py(OTel→SigNoz)가 담당.
- 그래프 config 에 callbacks=[handler] 를 붙이면 LangGraph 내부의 모든 .ainvoke 로
  자동 전파된다. 에이전트/노드/도구 코드는 건드리지 않는다.
- 기존 커스텀 트레이싱(chat_agent_traces, trace_node)과도 병행한다.
- fail-open: 클라이언트/핸들러 생성 실패가 앱 기동·요청을 막지 않는다.
- 토글: settings.langfuse_enabled=False(기본) 또는 키 미설정 시 완전 no-op(None 반환).

⚠️ 파일명은 langfuse_client.py 다 — langfuse.py 로 지으면 패키지를 shadow 한다.

infra 핸드오프(컨테이너 주입 env)는 core/config.py 의 Langfuse 섹션 주석 참조.
"""

import logging
from typing import Any

# langfuse v4 — 클라이언트 + LangChain CallbackHandler.
# 모듈 속성으로 노출 → 테스트에서 patch.object 가능.
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler

from core.config import settings

logger = logging.getLogger(__name__)

# shutdown 시 flush/shutdown 대상 클라이언트. (idempotency 가드 겸용)
_CLIENT: Langfuse | None = None
# 그래프가 attach 할 LangChain 콜백 핸들러.
_HANDLER: CallbackHandler | None = None


def init_langfuse() -> CallbackHandler | None:
    """Langfuse 클라이언트 + LangChain CallbackHandler 초기화.

    langfuse_enabled=False 또는 키 미설정 시 완전 no-op(None 반환).
    이미 초기화면 기존 핸들러를 재사용한다(idempotency).

    Returns:
        활성화되면 CallbackHandler, no-op(비활성/실패)이면 None.
    """
    global _CLIENT, _HANDLER

    if not settings.langfuse_enabled:
        logger.info("Langfuse 비활성 — 계측을 건너뜁니다 (langfuse_enabled 확인).")
        return None
    if not settings.langfuse_public_key or not settings.langfuse_secret_key:
        logger.info("Langfuse 키 미설정 — 계측을 건너뜁니다 (public/secret key 확인).")
        return None

    # idempotency 가드: 이미 활성이면 재초기화하지 않는다.
    if _HANDLER is not None:
        logger.info("Langfuse 이미 활성 — 중복 초기화를 건너뜁니다.")
        return _HANDLER

    try:
        # v4: Langfuse() 가 전역 클라이언트를 구성하고, CallbackHandler() 가 이를 사용.
        _CLIENT = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
            environment=settings.otel_environment,
            release=settings.app_version,
        )
        _HANDLER = CallbackHandler()
    except Exception:
        # fail-open: 계측 초기화 실패가 앱 기동을 막아서는 안 된다.
        logger.warning("Langfuse 초기화 실패 — 계측 없이 계속 진행합니다.", exc_info=True)
        _CLIENT = None
        _HANDLER = None
        return None

    logger.info(
        "Langfuse 활성 — host=%s env=%s",
        settings.langfuse_host,
        settings.otel_environment,
    )
    return _HANDLER


def get_langfuse_handler() -> Any:
    """그래프가 config 에 attach 할 때 쓰는 accessor.

    비활성/실패 시 None. 그래프는 None 이면 callbacks/metadata 를 붙이지 않는다.
    """
    return _HANDLER


def get_langfuse_client() -> Any:
    """enclosing span(Option 2) 진입·트레이스 I/O 설정에 쓰는 클라이언트 accessor.

    비활성/실패 시 None. 그래프는 client 와 handler 가 모두 있을 때만 enclosing span
    경로를 타고, 둘 중 하나라도 None 이면 기존(span/callback 미적용) 경로로 폴백한다.
    """
    return _CLIENT


def shutdown_langfuse() -> None:
    """클라이언트 flush + shutdown. lifespan 종료 시 호출 (best-effort)."""
    global _CLIENT, _HANDLER
    client = _CLIENT
    if client is not None:
        try:
            client.flush()
        except Exception:
            logger.warning("Langfuse flush 실패", exc_info=True)
        try:
            client.shutdown()
        except Exception:
            logger.warning("Langfuse shutdown 실패", exc_info=True)
    _CLIENT = None
    _HANDLER = None
