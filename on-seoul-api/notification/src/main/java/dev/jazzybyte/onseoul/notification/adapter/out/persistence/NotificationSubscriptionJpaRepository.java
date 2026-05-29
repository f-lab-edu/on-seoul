package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface NotificationSubscriptionJpaRepository
        extends JpaRepository<NotificationSubscriptionJpaEntity, Long> {

    List<NotificationSubscriptionJpaEntity> findAllByUserIdOrderByIdAsc(Long userId);

    /** keyset 페이지네이션: id > afterId ORDER BY id ASC LIMIT (pageable.pageSize). */
    List<NotificationSubscriptionJpaEntity> findByIdGreaterThanOrderByIdAsc(Long id, Pageable pageable);
}
