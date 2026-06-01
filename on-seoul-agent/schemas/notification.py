"""알림 템플릿 생성 API 스키마.

요청 본문은 서비스 그룹 리스트(services[])다. 조건 기반 구독 1건이
여러 service_id에 매칭되므로, 한 번의 호출에 여러 서비스 변경을 묶어 보낸다.

JSON 키는 snake_case 그대로(alias 없음)다. 단 changes[].field_name의 *값*은
collection(UpsertService)이 기록하는 camelCase 엔티티 필드명(예: serviceStatus,
receiptEndDt)이다 — 키는 snake_case, 값 문자열만 camelCase임에 유의.
"""

from typing import Literal

from pydantic import BaseModel, model_validator

ChangeType = Literal["NEW", "UPDATED", "DELETED"]
_MAX_SERVICES = 50
_MAX_CHANGES_PER_SERVICE = 50


class ChangeItem(BaseModel):
    change_type: ChangeType
    # JSON 키는 snake_case지만, 이 값은 collection이 기록하는
    # camelCase 엔티티 필드명(예: serviceStatus, receiptEndDt).
    field_name: str | None = None
    old_value: str | None = None
    new_value: str | None = None


class ServiceChangeGroup(BaseModel):
    service_id: str
    service_name: str | None = None
    service_url: str | None = None
    image_url: str | None = None
    place_name: str | None = None
    area_name: str | None = None
    service_status: str | None = None
    target_info: str | None = None
    receipt_start_dt: str | None = None
    receipt_end_dt: str | None = None
    changes: list[ChangeItem]

    @model_validator(mode="after")
    def validate_group(self) -> "ServiceChangeGroup":
        if not self.service_id.strip():
            raise ValueError("service_id는 비어 있을 수 없습니다.")
        if not self.changes:
            raise ValueError(
                f"service_id={self.service_id}의 changes는 최소 1건 이상이어야 합니다."
            )
        if len(self.changes) > _MAX_CHANGES_PER_SERVICE:
            raise ValueError(
                f"service_id={self.service_id}의 changes는 "
                f"{_MAX_CHANGES_PER_SERVICE}건을 초과할 수 없습니다. "
                f"(현재: {len(self.changes)})"
            )
        return self


class NotificationTemplateRequest(BaseModel):
    services: list[ServiceChangeGroup]

    @model_validator(mode="after")
    def validate_request(self) -> "NotificationTemplateRequest":
        if not self.services:
            raise ValueError("services는 최소 1개 이상이어야 합니다.")
        if len(self.services) > _MAX_SERVICES:
            raise ValueError(
                f"services는 {_MAX_SERVICES}개를 초과할 수 없습니다. "
                f"(현재: {len(self.services)})"
            )
        return self


class NotificationTemplateResponse(BaseModel):
    title: str
    body: str
