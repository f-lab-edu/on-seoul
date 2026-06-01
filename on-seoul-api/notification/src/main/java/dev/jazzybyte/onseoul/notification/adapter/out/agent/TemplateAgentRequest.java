package dev.jazzybyte.onseoul.notification.adapter.out.agent;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

/**
 * AI 서비스 {@code POST /notification/template} 요청 DTO.
 *
 * <p>ADR-0004 배치 모델: 한 구독에서 발견된 변경을 service_id 단위 그룹으로 묶어 1회 전송한다.
 * 와이어 포맷은 snake_case이며 null 필드는 직렬화에서 제외한다.
 */
record TemplateAgentRequest(
        @JsonProperty("services") List<ServiceChangeGroup> services
) {
    @JsonInclude(JsonInclude.Include.NON_NULL)
    record ServiceChangeGroup(
            @JsonProperty("service_id") String serviceId,
            @JsonProperty("service_name") String serviceName,
            @JsonProperty("service_url") String serviceUrl,
            @JsonProperty("image_url") String imageUrl,
            @JsonProperty("place_name") String placeName,
            @JsonProperty("area_name") String areaName,
            @JsonProperty("service_status") String serviceStatus,
            @JsonProperty("target_info") String targetInfo,
            @JsonProperty("receipt_start_dt") String receiptStartDt,
            @JsonProperty("receipt_end_dt") String receiptEndDt,
            @JsonProperty("changes") List<ChangeItem> changes
    ) {}

    @JsonInclude(JsonInclude.Include.NON_NULL)
    record ChangeItem(
            @JsonProperty("change_type") String changeType,
            @JsonProperty("field_name") String fieldName,
            @JsonProperty("old_value") String oldValue,
            @JsonProperty("new_value") String newValue
    ) {}
}
