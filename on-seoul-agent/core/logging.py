import logging

from core.config import settings

# 애플리케이션 로거 네임스페이스 — getLogger(__name__) 결과와 일치해야 한다.
_APP_NAMESPACES = ("routers", "agents", "core", "tools", "llm", "middleware", "scripts")

# 노이즈가 많은 서드파티 라이브러리 — WARNING 이상만 출력한다.
_QUIET_NAMESPACES = (
    "httpx",
    "httpcore",
    "langchain",
    "langchain_core",
    "langchain_google_genai",
    "openai",
    "google",
)


def setup_logging() -> None:
    """애플리케이션 로거를 설정한다.

    uvicorn은 기동 시 루트 로거를 WARNING으로 재설정하므로,
    애플리케이션 네임스페이스(routers.*, agents.* 등)에 직접 레벨을 지정한다.
    uvicorn 옵션 변경 없이 .env의 LOG_LEVEL만으로 제어된다.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler = logging.StreamHandler()
    handler.setFormatter(fmt)

    for namespace in _APP_NAMESPACES:
        lg = logging.getLogger(namespace)
        lg.setLevel(level)
        if not lg.handlers:
            lg.addHandler(handler)
        lg.propagate = False  # uvicorn 루트 로거 중복 출력 방지

    for namespace in _QUIET_NAMESPACES:
        logging.getLogger(namespace).setLevel(logging.WARNING)
