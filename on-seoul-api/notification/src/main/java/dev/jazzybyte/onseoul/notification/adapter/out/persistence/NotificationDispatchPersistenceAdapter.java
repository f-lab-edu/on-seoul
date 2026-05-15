package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveDispatchPort;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

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
     * Inserts a new dispatch row only if the (subscription_id, change_log_id) pair does not yet
     * exist. Returns the saved dispatch, or empty when a duplicate is detected.
     * Uses try-catch on DataIntegrityViolationException as a portable ON-CONFLICT-DO-NOTHING
     * equivalent that works in both PostgreSQL and the H2 test environment.
     */
    @Override
    public Optional<NotificationDispatch> saveIfAbsent(NotificationDispatch dispatch) {
        try {
            NotificationDispatchJpaEntity entity = new NotificationDispatchJpaEntity(
                    dispatch.getSubscriptionId(), dispatch.getChangeLogId());
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
                            dispatch.getSubscriptionId(), dispatch.getChangeLogId()));
        } else {
            entity = new NotificationDispatchJpaEntity(
                    dispatch.getSubscriptionId(), dispatch.getChangeLogId());
        }
        entity.applyDomain(
                dispatch.getStatus(), dispatch.getAttemptCount(),
                dispatch.getSentAt(), dispatch.getGeneratedTitle(),
                dispatch.getGeneratedBody(), dispatch.getTemplateSource(),
                dispatch.getLastError());
        return mapper.toDomain(repository.save(entity));
    }

    @Override
    @Transactional(readOnly = true)
    public Optional<NotificationDispatch> loadRetryable(Long subscriptionId, Long changeLogId,
                                                        int maxAttempts) {
        return repository.findRetryable(subscriptionId, changeLogId, maxAttempts)
                .map(mapper::toDomain);
    }
}
