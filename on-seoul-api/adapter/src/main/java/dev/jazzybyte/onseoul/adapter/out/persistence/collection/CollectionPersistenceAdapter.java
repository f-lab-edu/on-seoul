package dev.jazzybyte.onseoul.adapter.out.persistence.collection;

import dev.jazzybyte.onseoul.domain.model.CollectionHistory;
import dev.jazzybyte.onseoul.domain.model.ServiceChangeLog;
import dev.jazzybyte.onseoul.domain.port.out.SaveCollectionHistoryPort;
import dev.jazzybyte.onseoul.domain.port.out.SaveServiceChangeLogPort;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
class CollectionPersistenceAdapter implements SaveCollectionHistoryPort, SaveServiceChangeLogPort {

    private final CollectionHistoryJpaRepository historyRepository;
    private final ServiceChangeLogJpaRepository changeLogRepository;

    CollectionPersistenceAdapter(final CollectionHistoryJpaRepository historyRepository,
                                 final ServiceChangeLogJpaRepository changeLogRepository) {
        this.historyRepository = historyRepository;
        this.changeLogRepository = changeLogRepository;
    }

    @Override
    public CollectionHistory save(CollectionHistory history) {
        CollectionHistoryJpaEntity entity;
        if (history.getId() != null) {
            entity = historyRepository.findById(history.getId())
                    .orElseGet(() -> new CollectionHistoryJpaEntity(history.getSourceId(), history.getStatus()));
            entity.update(history.getStatus(), history.getTotalFetched(), history.getNewCount(),
                    history.getUpdatedCount(), history.getDeletedCount(),
                    history.getDurationMs(), history.getErrorMessage());
        } else {
            entity = new CollectionHistoryJpaEntity(history.getSourceId(), history.getStatus());
        }
        CollectionHistoryJpaEntity saved = historyRepository.save(entity);
        return toDomain(saved);
    }

    @Override
    public void saveAll(List<ServiceChangeLog> logs) {
        List<ServiceChangeLogJpaEntity> entities = logs.stream()
                .map(l -> new ServiceChangeLogJpaEntity(
                        l.getServiceId(), l.getCollectionId(), l.getChangeType(),
                        l.getFieldName(), l.getOldValue(), l.getNewValue()))
                .toList();
        changeLogRepository.saveAll(entities);
    }

    private CollectionHistory toDomain(CollectionHistoryJpaEntity e) {
        return new CollectionHistory(
                e.getId(), e.getSourceId(), e.getCollectedAt(),
                e.getStatus(), e.getTotalFetched(), e.getNewCount(),
                e.getUpdatedCount(), e.getDeletedCount(),
                e.getDurationMs(), e.getErrorMessage()
        );
    }
}
