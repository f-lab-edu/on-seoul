package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import org.springframework.stereotype.Component;

@Component
class NotificationPersistenceMapper {

    NotificationSubscription toDomain(NotificationSubscriptionJpaEntity e) {
        return new NotificationSubscription(
                e.getId(),
                e.getUserId(),
                e.getServiceId(),
                e.getFilter(),
                e.getLastNotifiedAt(),
                e.getCreatedAt());
    }

    NotificationDispatch toDomain(NotificationDispatchJpaEntity e) {
        return new NotificationDispatch(
                e.getId(),
                e.getSubscriptionId(),
                e.getChangeLogId(),
                e.getStatus(),
                e.getAttemptCount(),
                e.getSentAt(),
                e.getGeneratedTitle(),
                e.getGeneratedBody(),
                e.getTemplateSource(),
                e.getLastError(),
                e.getCreatedAt(),
                e.getUpdatedAt());
    }
}
