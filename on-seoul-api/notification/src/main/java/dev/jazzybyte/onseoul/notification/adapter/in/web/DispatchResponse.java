package dev.jazzybyte.onseoul.notification.adapter.in.web;

import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;

import java.time.Instant;

public record DispatchResponse(
        Long id,
        Long subscriptionId,
        String title,
        String body,
        DispatchStatus status,
        Instant sentAt
) {
    public static DispatchResponse from(NotificationDispatch d) {
        return new DispatchResponse(
                d.getId(),
                d.getSubscriptionId(),
                d.getGeneratedTitle(),
                d.getGeneratedBody(),
                d.getStatus(),
                d.getSentAt());
    }
}
