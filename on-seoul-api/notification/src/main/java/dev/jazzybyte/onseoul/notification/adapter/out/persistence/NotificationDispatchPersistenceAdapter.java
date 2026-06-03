package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import com.fasterxml.jackson.databind.node.JsonNodeFactory;
import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveDispatchPort;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

@Component
class NotificationDispatchPersistenceAdapter
        implements SaveDispatchPort, LoadDispatchPort {

    private final NotificationDispatchJpaRepository repository;
    private final NotificationPersistenceMapper mapper;

    NotificationDispatchPersistenceAdapter(
            final NotificationDispatchJpaRepository repository,
            final NotificationPersistenceMapper mapper) {
        this.repository = repository;
        this.mapper = mapper;
    }

    /**
     * (batch_id, subscription_id) UNIQUE 제약을 멱등 INSERT로 활용한다.
     * 중복일 경우 DataIntegrityViolationException을 catch하여 empty 반환 — H2/PG 공통 동작.
     */
    @Override
    public Optional<NotificationDispatch> saveIfAbsent(NotificationDispatch dispatch) {
        try {
            // CHANGE dispatch 도 dispatch_date(UTC today)를 채운다 — cross-trigger dedup 선조회 기준.
            NotificationDispatchJpaEntity entity = new NotificationDispatchJpaEntity(
                    dispatch.getBatchId(), dispatch.getSubscriptionId(), dispatch.getDispatchDate());
            return Optional.of(mapper.toDomain(repository.saveAndFlush(entity)));
        } catch (DataIntegrityViolationException e) {
            return Optional.empty();
        }
    }

    /**
     * 시점 트리거 dispatch 멱등 INSERT.
     * 부분 unique 인덱스 {@code uq_nd_scheduled_dedup (subscription_id, service_id, dispatch_date)
     * WHERE service_id IS NOT NULL} 충돌 시 {@link DataIntegrityViolationException} 을 catch 하여
     * empty 를 반환한다(H2/PG 공통 동작 — saveIfAbsent 와 동일 패턴).
     */
    @Override
    public Optional<NotificationDispatch> saveScheduledIfAbsent(NotificationDispatch dispatch) {
        try {
            NotificationDispatchJpaEntity entity = new NotificationDispatchJpaEntity(
                    dispatch.getBatchId(), dispatch.getSubscriptionId(),
                    dispatch.getTriggerType(), dispatch.getServiceId(), dispatch.getDispatchDate());
            return Optional.of(mapper.toDomain(repository.saveAndFlush(entity)));
        } catch (DataIntegrityViolationException e) {
            return Optional.empty();
        }
    }

    @Override
    public NotificationDispatch save(NotificationDispatch dispatch) {
        NotificationDispatchJpaEntity entity;
        if (dispatch.getId() != null) {
            entity = repository.findById(dispatch.getId())
                    .orElseGet(() -> new NotificationDispatchJpaEntity(
                            dispatch.getBatchId(), dispatch.getSubscriptionId()));
        } else {
            entity = new NotificationDispatchJpaEntity(
                    dispatch.getBatchId(), dispatch.getSubscriptionId());
        }
        entity.applyDomain(
                dispatch.getStatus(),
                dispatch.getSentAt(), dispatch.getGeneratedTitle(),
                dispatch.getGeneratedBody(), dispatch.getTemplateSource(),
                dispatch.getLastError(), dispatch.getAttemptCount(),
                dispatch.getNotificationPayload());
        return mapper.toDomain(repository.save(entity));
    }

    @Override
    @Transactional(readOnly = true)
    public Optional<NotificationDispatch> loadByBatchAndSubscription(Long batchId, Long subscriptionId) {
        return repository.findByBatchIdAndSubscriptionId(batchId, subscriptionId)
                .map(mapper::toDomain);
    }

    @Override
    @Transactional(readOnly = true)
    public List<NotificationDispatch> loadByUserId(Long userId, Long cursor, int limit) {
        return repository.findByUserIdWithCursor(userId, cursor, PageRequest.of(0, limit)).stream()
                .map(mapper::toDomain)
                .toList();
    }

    /**
     * FAILED dispatch 중 attempt_count < MAX_ATTEMPTS 이고
     * updatedAt이 RETRY_COOLDOWN 이상 지난 것만 반환한다.
     * cooldown은 메인 배치(5분 주기)와의 레이스 컨디션을 최소화한다.
     */
    static final Duration RETRY_COOLDOWN = Duration.ofMinutes(10);

    @Override
    @Transactional(readOnly = true)
    public List<NotificationDispatch> findRetryable() {
        return findRetryable(Instant.now().minus(RETRY_COOLDOWN));
    }

    /**
     * 테스트 및 오버라이드용: cooldown 기준 시각을 직접 지정할 수 있는 내부 메서드.
     * 프로덕션에서는 {@link #findRetryable()} 사용.
     *
     * @param cooldownInstant 이 시각 이전에 업데이트된 dispatch만 반환 (미래 시각 전달 시 cooldown 미적용)
     */
    List<NotificationDispatch> findRetryable(Instant cooldownInstant) {
        return repository.findRetryable(DispatchStatus.FAILED, NotificationDispatch.MAX_ATTEMPTS, cooldownInstant)
                .stream()
                .map(mapper::toDomain)
                .toList();
    }

    /**
     * 해당 구독의 DEAD dispatch 존재 여부를 조회한다.
     *
     * <p>{@code propagation = REQUIRED} 로 호출자(txA의 REQUIRES_NEW TX)에 합류한다.
     * 별도 스냅샷이 아닌 현재 TX 뷰를 공유하므로 TOCTOU 가능성이 이론적으로 존재하지만,
     * 메인 배치 스케줄러의 Semaphore(4) + 구독별 단일 순회 구조상 동일 subscriptionId를
     * 두 스레드가 동시에 처리하지 않아 실운영에서 레이스 컨디션은 발생하지 않는다.
     */
    @Override
    @Transactional(readOnly = true, propagation = Propagation.REQUIRED)
    public boolean existsDeadDispatchBySubscriptionId(Long subscriptionId) {
        return repository.existsBySubscriptionIdAndStatus(subscriptionId, DispatchStatus.DEAD);
    }

    @Override
    @Transactional(readOnly = true)
    public boolean existsChangeDispatchForServiceToday(Long subscriptionId, String serviceId,
                                                       LocalDate dispatchDate) {
        if (subscriptionId == null || serviceId == null || serviceId.isBlank() || dispatchDate == null) {
            return false;
        }
        return repository.existsChangeDispatchCoveringService(
                subscriptionId, dispatchDate, servicesContainmentLiteral(serviceId));
    }

    @Override
    @Transactional(readOnly = true)
    public boolean existsScheduledDispatch(Long subscriptionId, String serviceId, LocalDate dispatchDate) {
        if (subscriptionId == null || serviceId == null || serviceId.isBlank() || dispatchDate == null) {
            return false;
        }
        return repository.existsBySubscriptionIdAndServiceIdAndDispatchDate(
                subscriptionId, serviceId, dispatchDate);
    }

    /**
     * JSONB containment 우변 리터럴 {@code [{"serviceId":"<sid>"}]} 을 만든다.
     * serviceId 의 따옴표/제어문자를 안전하게 이스케이프하기 위해 JsonNode 직렬화로 구성한다
     * (배열 안 객체여야 payload->'services'(배열) 와 {@code @>} 비교가 성립한다).
     */
    static String servicesContainmentLiteral(String serviceId) {
        return JsonNodeFactory.instance.arrayNode()
                .add(JsonNodeFactory.instance.objectNode().put("serviceId", serviceId))
                .toString();
    }
}
