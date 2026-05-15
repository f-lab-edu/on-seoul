package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Slf4j
@Component
class NotificationSubscriptionPersistenceAdapter
        implements LoadSubscriptionPort, SaveSubscriptionPort {

    private final NotificationSubscriptionJpaRepository repository;
    private final NotificationPersistenceMapper mapper;
    private final JdbcTemplate jdbcTemplate;

    NotificationSubscriptionPersistenceAdapter(
            final NotificationSubscriptionJpaRepository repository,
            final NotificationPersistenceMapper mapper,
            final JdbcTemplate jdbcTemplate) {
        this.repository = repository;
        this.mapper = mapper;
        this.jdbcTemplate = jdbcTemplate;
    }

    @Override
    @Transactional(readOnly = true)
    public List<NotificationSubscription> loadAll() {
        return repository.findAll().stream()
                .map(mapper::toDomain)
                .toList();
    }

    @Override
    @Transactional
    public void saveIfAbsent(NotificationSubscription subscription) {
        // JdbcTemplate은 JPA 영속성 컨텍스트를 오염시키지 않으므로
        // DataIntegrityViolationException(uq_ns_user_service 중복)을 catch해도 TX가 rollback-only로 마킹되지 않음.
        try {
            String channelsJson = mapper.serializeChannels(subscription.getChannels());
            jdbcTemplate.update(
                    "INSERT INTO notification_subscriptions (user_id, service_id, filter, channels, created_at) VALUES (?, ?, ?, ?, NOW())",
                    subscription.getUserId(),
                    subscription.getServiceId(),
                    subscription.getFilter(),
                    channelsJson);
        } catch (DataIntegrityViolationException e) {
            // uq_ns_user_service 중복 — 이미 존재하므로 무시
            log.debug("[Notification] 구독 이미 존재 — skip: userId={}, serviceId={}",
                    subscription.getUserId(), subscription.getServiceId());
        }
    }

    @Override
    public NotificationSubscription save(NotificationSubscription subscription) {
        NotificationSubscriptionJpaEntity entity;
        String channelsJson = mapper.serializeChannels(subscription.getChannels());
        if (subscription.getId() != null) {
            entity = repository.findById(subscription.getId())
                    .orElseGet(() -> new NotificationSubscriptionJpaEntity(
                            subscription.getUserId(),
                            subscription.getServiceId(),
                            subscription.getFilter(),
                            channelsJson));
            entity.updateLastNotifiedAt(subscription.getLastNotifiedAt());
        } else {
            entity = new NotificationSubscriptionJpaEntity(
                    subscription.getUserId(),
                    subscription.getServiceId(),
                    subscription.getFilter(),
                    channelsJson);
        }
        return mapper.toDomain(repository.save(entity));
    }
}
