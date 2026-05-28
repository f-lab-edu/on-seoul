package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface NotificationSubscriptionJpaRepository
        extends JpaRepository<NotificationSubscriptionJpaEntity, Long> {

    List<NotificationSubscriptionJpaEntity> findAllByUserIdOrderByIdAsc(Long userId);
}
