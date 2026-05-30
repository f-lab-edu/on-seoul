package dev.jazzybyte.onseoul.notification.adapter.in.web;

import dev.jazzybyte.onseoul.notification.application.SubscriptionView;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;

import java.time.Instant;
import java.util.Set;
import java.util.stream.Collectors;

public record SubscriptionResponse(
        Long id,
        String serviceId,
        FilterDto filter,
        Set<String> channels,
        Instant lastNotifiedAt,
        Instant createdAt
) {
    public static SubscriptionResponse from(SubscriptionView view) {
        Set<String> channels = view.channels().stream()
                .map(NotificationChannel::name)
                .collect(Collectors.toCollection(java.util.LinkedHashSet::new));
        return new SubscriptionResponse(
                view.id(),
                view.serviceId(),
                FilterDto.fromDomain(view.filter()),
                channels,
                view.lastNotifiedAt(),
                view.createdAt());
    }
}
