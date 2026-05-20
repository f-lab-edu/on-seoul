"""임베딩 동기화 API 스키마."""

import re

from pydantic import BaseModel, model_validator

SERVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_ITEMS = 500


class ServiceEmbeddingsSyncRequest(BaseModel):
    upsert: list[str] = []
    delete: list[str] = []

    @model_validator(mode="after")
    def validate_request(self) -> "ServiceEmbeddingsSyncRequest":
        if not self.upsert and not self.delete:
            raise ValueError("upsert와 delete 중 하나 이상에 service_id를 지정해야 합니다.")

        total = len(self.upsert) + len(self.delete)
        if total > _MAX_ITEMS:
            raise ValueError(
                f"upsert + delete 합계가 {_MAX_ITEMS}개를 초과할 수 없습니다. (현재: {total})"
            )

        overlap = set(self.upsert) & set(self.delete)
        if overlap:
            raise ValueError(
                f"upsert와 delete에 동시에 포함된 service_id가 있습니다: {sorted(overlap)}"
            )

        for sid in self.upsert + self.delete:
            if not SERVICE_ID_PATTERN.match(sid):
                raise ValueError(
                    f"유효하지 않은 service_id 형식입니다: {sid!r}. "
                    "영문자, 숫자, '_', '-'만 허용됩니다."
                )

        return self


class ServiceEmbeddingsSyncResponse(BaseModel):
    accepted: dict[str, int]  # {"upsert": N, "delete": M}
