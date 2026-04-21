from enum import Enum
from typing import Any

from pydantic import BaseModel


class EventType(str, Enum):
    AGENT_START = "agent_start"
    TOOL_CALL = "tool_call"
    TOKEN = "token"
    DONE = "done"
    ERROR = "error"


class SSEEvent(BaseModel):
    event: EventType
    data: Any = None
    message_id: int | None = None
