package dev.jazzybyte.onseoul.notification.domain;

import java.time.Instant;

/**
 * notification BC 전용 값 객체. collection BC의 ServiceChangeLog를 직접 import하지 않기 위해 정의한다.
 */
public record ServiceChange(
        Long id,
        String serviceId,
        String changeType,
        String fieldName,
        String oldValue,
        String newValue,
        Instant changedAt
) {}
