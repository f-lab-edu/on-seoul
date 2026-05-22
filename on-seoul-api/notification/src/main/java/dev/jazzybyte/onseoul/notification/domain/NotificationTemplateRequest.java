package dev.jazzybyte.onseoul.notification.domain;

import java.util.List;

/**
 * AI 서비스 {@code POST /notification/template} 호출 요청 (ADR-0004 배치 모델).
 *
 * <p>한 구독에 대해 발견된 모든 변경 이벤트를 List로 묶어 1회 호출한다 — 구독 1건당 AI 호출 1회.
 */
public record NotificationTemplateRequest(
        String serviceId,
        List<ChangeItem> changes
) {
    public NotificationTemplateRequest {
        changes = changes == null ? List.of() : List.copyOf(changes);
    }

    /** 단일 변경 이벤트. */
    public record ChangeItem(
            String changeType,
            String fieldName,
            String oldValue,
            String newValue
    ) {}
}
