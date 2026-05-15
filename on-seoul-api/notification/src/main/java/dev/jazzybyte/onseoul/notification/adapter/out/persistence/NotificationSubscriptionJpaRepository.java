package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

public interface NotificationSubscriptionJpaRepository
        extends JpaRepository<NotificationSubscriptionJpaEntity, Long> {
}
