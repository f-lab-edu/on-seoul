"""알림 템플릿 생성 API 스키마."""

from typing import Literal

from pydantic import BaseModel, model_validator

ChangeType = Literal["NEW", "UPDATED", "DELETED"]
_MAX_CHANGES = 50


class ServiceChange(BaseModel):
    change_type: ChangeType
    field_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None


class NotificationTemplateRequest(BaseModel):
    service_id: str
    changes: list[ServiceChange]

    @model_validator(mode="after")
    def validate_request(self) -> "NotificationTemplateRequest":
        if not self.service_id.strip():
            raise ValueError("service_id는 비어 있을 수 없습니다.")
        if not self.changes:
            raise ValueError("changes는 최소 1건 이상이어야 합니다.")
        if len(self.changes) > _MAX_CHANGES:
            raise ValueError(
                f"changes는 {_MAX_CHANGES}건을 초과할 수 없습니다. (현재: {len(self.changes)})"
            )
        return self


class NotificationTemplateResponse(BaseModel):
    title: str
    body: str
