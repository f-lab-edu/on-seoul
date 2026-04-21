from typing import Any


class OnSeoulAgentException(Exception):
    """AI 서비스 기본 예외 클래스"""

    def __init__(self, message: str, detail: Any | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail


class LLMException(OnSeoulAgentException):
    """LLM 벤더(Gemini, OpenAI 등) 호출 관련 예외"""

    pass


class DatabaseException(OnSeoulAgentException):
    """DB(on_ai, on_data) 연동 및 쿼리 관련 예외"""

    pass


class WorkflowException(OnSeoulAgentException):
    """LangGraph 워크플로우 실행 및 노드 간 로직 예외"""

    pass


class ConfigurationException(OnSeoulAgentException):
    """환경 변수 및 설정 관련 예외"""

    pass
