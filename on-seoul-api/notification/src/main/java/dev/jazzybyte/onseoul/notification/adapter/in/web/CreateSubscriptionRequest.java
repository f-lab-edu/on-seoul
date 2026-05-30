package dev.jazzybyte.onseoul.notification.adapter.in.web;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.in.CreateSubscriptionUseCase.CreateSubscriptionCommand;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;

import java.util.Set;

public record CreateSubscriptionRequest(
        @NotBlank String serviceId,
        FilterDto filter,
        @NotEmpty Set<NotificationChannel> channels
) {
    public CreateSubscriptionCommand toCommand() {
        SubscriptionFilter f = filter != null ? filter.toDomain() : SubscriptionFilter.empty();
        return new CreateSubscriptionCommand(serviceId, f, channels);
    }
}
