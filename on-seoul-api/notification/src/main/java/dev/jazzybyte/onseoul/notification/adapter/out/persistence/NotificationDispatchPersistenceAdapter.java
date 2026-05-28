package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveDispatchPort;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

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
            NotificationDispatchJpaEntity entity = new NotificationDispatchJpaEntity(
                    dispatch.getBatchId(), dispatch.getSubscriptionId());
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
                dispatch.getLastError());
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
}
