package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import org.springframework.data.jpa.repository.JpaRepository;

public interface NotificationBatchJpaRepository
        extends JpaRepository<NotificationBatchJpaEntity, Long> {
}
