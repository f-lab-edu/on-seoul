package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.BatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.port.out.LoadBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveBatchPort;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

@Component
class NotificationBatchPersistenceAdapter implements SaveBatchPort, LoadBatchPort {

    private final NotificationBatchJpaRepository repository;

    NotificationBatchPersistenceAdapter(final NotificationBatchJpaRepository repository) {
        this.repository = repository;
    }

    @Override
    @Transactional
    public NotificationBatch insertRunning(NotificationBatch batch) {
        NotificationBatchJpaEntity entity = new NotificationBatchJpaEntity(
                batch.getStartedAt(), batch.getStatus());
        NotificationBatchJpaEntity saved = repository.saveAndFlush(entity);
        return toDomain(saved);
    }

    @Override
    @Transactional
    public NotificationBatch update(NotificationBatch batch) {
        NotificationBatchJpaEntity entity = repository.findById(batch.getId())
                .orElseThrow(() -> new IllegalStateException(
                        "NotificationBatch not found: id=" + batch.getId()));
        entity.apply(batch.getStatus(), batch.getFinishedAt(),
                batch.getSentCount(), batch.getFailedCount());
        return toDomain(repository.save(entity));
    }

    @Override
    @Transactional(readOnly = true)
    public Optional<NotificationBatch> loadById(Long batchId) {
        return repository.findById(batchId).map(this::toDomain);
    }

    @Override
    @Transactional(readOnly = true)
    public List<NotificationBatch> findStaleRunning(Instant staleBefore) {
        return repository.findByStatusAndStartedAtBefore(BatchStatus.RUNNING, staleBefore)
                .stream().map(this::toDomain).toList();
    }

    private NotificationBatch toDomain(NotificationBatchJpaEntity e) {
        return new NotificationBatch(
                e.getId(), e.getStartedAt(), e.getFinishedAt(),
                e.getStatus(), e.getSentCount(), e.getFailedCount());
    }
}
