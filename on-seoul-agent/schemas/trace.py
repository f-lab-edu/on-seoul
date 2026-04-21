from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class AgentTrace(BaseModel):
    message_id: int
    trace: dict[str, Any]
    crreated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
