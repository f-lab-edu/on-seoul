package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.PageRequest;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;
import java.util.Set;

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
    @Deprecated
    @Transactional(readOnly = true)
    public List<NotificationSubscription> loadAll() {
        return repository.findAll().stream()
                .map(mapper::toDomain)
                .toList();
    }

    @Override
    @Transactional(readOnly = true)
    public List<NotificationSubscription> loadChunk(Long afterId, int limit) {
        return repository.findByIdGreaterThanOrderByIdAsc(afterId, PageRequest.of(0, limit))
                .stream().map(mapper::toDomain).toList();
    }

    @Override
    @Transactional(readOnly = true)
    public List<NotificationSubscription> loadByUserId(Long userId) {
        return repository.findAllByUserIdOrderByIdAsc(userId).stream()
                .map(mapper::toDomain)
                .toList();
    }

    @Override
    @Transactional(readOnly = true)
    public Optional<NotificationSubscription> loadById(Long id) {
        return repository.findById(id).map(mapper::toDomain);
    }

    @Override
    @Transactional
    public NotificationSubscription insert(NotificationSubscription subscription) {
        // saveAndFlush 로 즉시 INSERT 하여 uq_ns_user_service 위반을 호출 시점에 노출시킨다.
        // DataIntegrityViolationException 은 application service 에서 SUBSCRIPTION_CONFLICT 로 변환.
        String channelsJson = mapper.serializeChannels(subscription.getChannels());
        String filterJson = resolveFilterJson(subscription);
        NotificationSubscriptionJpaEntity entity = new NotificationSubscriptionJpaEntity(
                subscription.getUserId(),
                subscription.getServiceId(),
                filterJson,
                channelsJson);
        return mapper.toDomain(repository.saveAndFlush(entity));
    }

    @Override
    @Transactional
    public NotificationSubscription updatePartial(Long id, SubscriptionFilter filter, Set<NotificationChannel> channels) {
        NotificationSubscriptionJpaEntity entity = repository.findById(id)
                .orElseThrow(() -> new IllegalStateException(
                        "updatePartial 호출 시 구독을 찾을 수 없음: id=" + id));
        if (filter != null) {
            entity.updateFilter(mapper.serialize(filter));
        }
        if (channels != null) {
            entity.updateChannels(mapper.serializeChannels(channels));
        }
        return mapper.toDomain(repository.save(entity));
    }

    /**
     * 새 구독 INSERT 시 filter JSON 결정 규칙.
     * <ul>
     *   <li>도메인이 이미 JSON 문자열을 들고 있으면 그대로 사용 (예: legacy save 경로).</li>
     *   <li>도메인이 {@link SubscriptionFilter} (parsedFilter) 만 들고 있으면 mapper 로 직렬화.</li>
     *   <li>둘 다 없으면 {@code "{}"} 로 폴백.</li>
     * </ul>
     */
    private String resolveFilterJson(NotificationSubscription subscription) {
        if (subscription.getFilter() != null) {
            return subscription.getFilter();
        }
        SubscriptionFilter parsed = subscription.getParsedFilter();
        return mapper.serialize(parsed != null ? parsed : SubscriptionFilter.empty());
    }

    @Override
    @Transactional
    public void deleteById(Long id) {
        repository.deleteById(id);
    }

    @Override
    @Transactional
    public void saveIfAbsent(NotificationSubscription subscription) {
        // JdbcTemplate은 JPA 영속성 컨텍스트를 오염시키지 않으므로
        // DataIntegrityViolationException(uq_ns_user_service 중복)을 catch해도 TX가 rollback-only로 마킹되지 않음.
        try {
            String channelsJson = mapper.serializeChannels(subscription.getChannels());
            String filterJson = resolveFilterJson(subscription);
            jdbcTemplate.update(
                    "INSERT INTO notification_subscriptions (user_id, service_id, filter, channels, created_at) VALUES (?, ?, ?, ?, NOW())",
                    subscription.getUserId(),
                    subscription.getServiceId(),
                    filterJson,
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
        String filterJson = resolveFilterJson(subscription);
        if (subscription.getId() != null) {
            entity = repository.findById(subscription.getId())
                    .orElseGet(() -> new NotificationSubscriptionJpaEntity(
                            subscription.getUserId(),
                            subscription.getServiceId(),
                            filterJson,
                            channelsJson));
            entity.updateLastNotifiedAt(subscription.getLastNotifiedAt());
        } else {
            entity = new NotificationSubscriptionJpaEntity(
                    subscription.getUserId(),
                    subscription.getServiceId(),
                    filterJson,
                    channelsJson);
        }
        return mapper.toDomain(repository.save(entity));
    }
}
