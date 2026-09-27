package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.PageRequest;
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

    NotificationSubscriptionPersistenceAdapter(
            final NotificationSubscriptionJpaRepository repository,
            final NotificationPersistenceMapper mapper) {
        this.repository = repository;
        this.mapper = mapper;
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
        // 구독 중복 방지 제약은 제거되었다 — 한 user_id가 여러 조건 기반 구독을 가질 수 있다.
        // saveAndFlush 로 즉시 INSERT 하여 DB 제약 위반(있다면)을 호출 시점에 노출시킨다.
        String channelsJson = mapper.serializeChannels(subscription.getChannels());
        String filterJson = resolveFilterJson(subscription);
        NotificationSubscriptionJpaEntity entity = new NotificationSubscriptionJpaEntity(
                subscription.getUserId(),
                filterJson,
                channelsJson);
        return mapper.toDomain(repository.saveAndFlush(entity));
    }

    @Override
    @Transactional
    public NotificationSubscription updatePartial(Long id, SubscriptionFilter filter, Set<NotificationChannel> channels) {
        // 소프트 딜리트 도입 후 도달 가능해진 경로: NotificationSubscriptionService.update() 는 단일
        // 트랜잭션(READ COMMITTED)이라 loadOwned 통과 후 동시 DELETE 가 커밋되면 여기서 empty 다.
        // 단정문(IllegalStateException=500) 대신 loadOwned 와 같은 404 로 응답한다.
        NotificationSubscriptionJpaEntity entity = repository.findById(id)
                .orElseThrow(() -> new OnSeoulApiException(ErrorCode.SUBSCRIPTION_NOT_FOUND));
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

    /**
     * 호출처는 {@link dev.jazzybyte.onseoul.notification.application.NotificationTxHelper} 의
     * {@code txBSuccess} / {@code txBRetrySuccess} 두 곳뿐이고, 둘 다 발송 성공 후
     * {@code last_notified_at} 커서를 전진시키는 용도다.
     *
     * <p>id 가 있는데 조회가 empty 면 배치 진행 중에 사용자가 구독을 해지(소프트 딜리트)한 것이다.
     * 전진시킬 커서 대상이 없으므로 <b>no-op</b> 으로 끝낸다 — 새 행을 INSERT 하면 해지한 구독이
     * 활성 상태로 부활해 이후 모든 배치가 계속 발송한다. 예외를 던지지 않는 이유는 정상적인 해지가
     * 배치 실패로 이어져 dispatch 가 FAILED 로 찍히고 불필요한 재시도까지 유발하기 때문이다
     * ({@code DispatchRetryScheduler} 의 "구독 삭제됐으면 skip" 의미와 일치).
     *
     * <p>{@code @Transactional} 은 가드가 자기완결적이기 위해 필요하다 — 트랜잭션 밖에서 호출되면
     * {@code findById} 가 자체 트랜잭션에서 끝나 엔티티가 detached 되고, {@code repository.save} 가
     * {@code merge()} 로 동작해 내부 SELECT 가 {@code @SQLRestriction} 때문에 empty →
     * transient 로 오인한 INSERT 가 다시 발생한다.
     */
    @Override
    @Transactional
    public NotificationSubscription save(NotificationSubscription subscription) {
        NotificationSubscriptionJpaEntity entity;
        String channelsJson = mapper.serializeChannels(subscription.getChannels());
        String filterJson = resolveFilterJson(subscription);
        if (subscription.getId() != null) {
            Optional<NotificationSubscriptionJpaEntity> found = repository.findById(subscription.getId());
            if (found.isEmpty()) {
                log.info("[Notification] 구독이 삭제됨 — last_notified_at 전진 생략: id={}",
                        subscription.getId());
                return subscription;
            }
            entity = found.get();
            entity.updateLastNotifiedAt(subscription.getLastNotifiedAt());
        } else {
            entity = new NotificationSubscriptionJpaEntity(
                    subscription.getUserId(),
                    filterJson,
                    channelsJson);
        }
        return mapper.toDomain(repository.save(entity));
    }
}
