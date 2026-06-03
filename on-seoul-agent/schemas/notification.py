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
TriggerType = Literal["CHANGE", "OPEN_DAY", "BEFORE_RECEIPT_D1", "DEADLINE_DDAY"]
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
        # "changes 최소 1건" 검증은 trigger_type을 알아야 하므로 요청 레벨
        # (NotificationTemplateRequest.validate_request)로 옮겼다. 시점 트리거는
        # changes가 빈 배열로 온다. 그룹 레벨에는 service_id 비어있음·상한 검증만 둔다.
        if not self.service_id.strip():
            raise ValueError("service_id는 비어 있을 수 없습니다.")
        if len(self.changes) > _MAX_CHANGES_PER_SERVICE:
            raise ValueError(
                f"service_id={self.service_id}의 changes는 "
                f"{_MAX_CHANGES_PER_SERVICE}건을 초과할 수 없습니다. "
                f"(현재: {len(self.changes)})"
            )
        return self


class NotificationTemplateRequest(BaseModel):
    # 누락 시 CHANGE 기본(하위호환). 시점 트리거는 changes 빈 배열을 허용한다.
    trigger_type: TriggerType = "CHANGE"
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
        # CHANGE는 변경 이벤트 기반이므로 그룹당 changes 최소 1건이 필요하다.
        # 시점 트리거(OPEN_DAY/BEFORE_RECEIPT_D1/DEADLINE_DDAY)는 빈 changes 허용.
        if self.trigger_type == "CHANGE":
            for g in self.services:
                if not g.changes:
                    raise ValueError(
                        f"CHANGE 트리거의 service_id={g.service_id} changes는 "
                        "최소 1건이어야 합니다."
                    )
        return self


class NotificationTemplateResponse(BaseModel):
    """알림 응답.

    title은 코드에서 결정적으로 생성한다(날짜·서비스 건수 기반). summary만 LLM이
    생성하며, 사실 재나열이 아니라 짧은 요약/하이라이트다 — 서비스명·상태·접수기간
    ·링크 같은 사실은 소비자(API 서비스)의 Knock 이메일 Liquid 템플릿이
    결정적으로 렌더링한다.
    """

    title: str
    summary: str
