package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
class NotificationSubscriptionPersistenceAdapter
        implements LoadSubscriptionPort, SaveSubscriptionPort {

    private final NotificationSubscriptionJpaRepository repository;
    private final NotificationPersistenceMapper mapper;

    NotificationSubscriptionPersistenceAdapter(
            final NotificationSubscriptionJpaRepository repository,
            final NotificationPersistenceMapper mapper) {
        this.repository = repository;
        this.mapper = mapper;
    }

    @Override
    public List<NotificationSubscription> loadAll() {
        return repository.findAll().stream()
                .map(mapper::toDomain)
                .toList();
    }

    @Override
    public NotificationSubscription save(NotificationSubscription subscription) {
        NotificationSubscriptionJpaEntity entity;
        if (subscription.getId() != null) {
            entity = repository.findById(subscription.getId())
                    .orElseGet(() -> new NotificationSubscriptionJpaEntity(
                            subscription.getUserId(),
                            subscription.getServiceId(),
                            subscription.getFilter()));
            entity.updateLastNotifiedAt(subscription.getLastNotifiedAt());
        } else {
            entity = new NotificationSubscriptionJpaEntity(
                    subscription.getUserId(),
                    subscription.getServiceId(),
                    subscription.getFilter());
        }
        return mapper.toDomain(repository.save(entity));
    }
}
