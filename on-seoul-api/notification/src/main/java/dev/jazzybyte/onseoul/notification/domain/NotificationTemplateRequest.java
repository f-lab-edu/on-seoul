package dev.jazzybyte.onseoul.notification.domain;

public record NotificationTemplateRequest(
        String serviceId,
        String changeType,
        String fieldName,
        String oldValue,
        String newValue
) {}
