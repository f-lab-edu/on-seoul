package dev.jazzybyte.onseoul.notification.adapter.out.agent;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * AI 서비스 {@code POST /notification/template} 요청 DTO.
 *
 * <p>ADR-0004 배치 모델: 한 구독에서 발견된 모든 변경을 List로 묶어 1회 전송한다.
 */
record TemplateAgentRequest(
        @JsonProperty("service_id") String serviceId,
        @JsonProperty("changes") List<ChangeItem> changes
) {
    record ChangeItem(
            @JsonProperty("change_type") String changeType,
            @JsonProperty("field_name") String fieldName,
            @JsonProperty("old_value") String oldValue,
            @JsonProperty("new_value") String newValue
    ) {}
}
