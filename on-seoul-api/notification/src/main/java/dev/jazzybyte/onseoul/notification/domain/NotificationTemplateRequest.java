package dev.jazzybyte.onseoul.notification.domain;

import java.util.List;

/**
 * AI 서비스 {@code POST /notification/template} 호출 요청 (ADR-0004 배치 모델).
 *
 * <p>구독 1건당 AI 호출 1회. 하나의 구독 필터가 여러 service_id에 동시 매칭될 수 있으므로
 * 변경을 service_id 단위로 그룹핑해 {@link ServiceChangeGroup} 리스트로 묶어 전송한다.
 * 각 그룹은 해당 서비스의 메타(serviceName/serviceUrl 등)와 변경 목록을 함께 담는다.
 */
public record NotificationTemplateRequest(
        TriggerType triggerType,
        List<ServiceChangeGroup> services
) {
    public NotificationTemplateRequest {
        triggerType = triggerType == null ? TriggerType.CHANGE : triggerType;
        services = services == null ? List.of() : List.copyOf(services);
    }

    /** 기존 CHANGE 경로 편의 생성자 — triggerType=CHANGE. */
    public NotificationTemplateRequest(List<ServiceChangeGroup> services) {
        this(TriggerType.CHANGE, services);
    }

    /** 한 service_id에 대한 메타 + 변경 목록. */
    public record ServiceChangeGroup(
            String serviceId,
            String serviceName,
            String serviceUrl,
            String imageUrl,
            String placeName,
            String areaName,
            String serviceStatus,
            String targetInfo,
            String receiptStartDt,
            String receiptEndDt,
            List<ChangeItem> changes
    ) {
        public ServiceChangeGroup {
            changes = changes == null ? List.of() : List.copyOf(changes);
        }
    }

    /** 단일 변경 이벤트. */
    public record ChangeItem(
            String changeType,
            String fieldName,
            String oldValue,
            String newValue
    ) {}
}
