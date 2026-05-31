package dev.jazzybyte.onseoul.collection.adapter.out.persistence.collection;

import dev.jazzybyte.onseoul.collection.domain.ChangeType;
import dev.jazzybyte.onseoul.collection.domain.CollectionHistory;
import dev.jazzybyte.onseoul.collection.domain.ServiceChangeLog;
import dev.jazzybyte.onseoul.collection.port.out.LoadChangedServiceIdsPort;
import dev.jazzybyte.onseoul.collection.port.out.SaveCollectionHistoryPort;
import dev.jazzybyte.onseoul.collection.port.out.SaveServiceChangeLogPort;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;

@Component
class CollectionPersistenceAdapter
        implements SaveCollectionHistoryPort, SaveServiceChangeLogPort, LoadChangedServiceIdsPort {

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

    @Override
    public ChangedServiceIds loadSince(Instant since) {
        // changed_at은 LocalDateTime 컬럼이므로 시스템 기본 존으로 변환해 비교한다
        // (@CreationTimestamp가 LocalDateTime.now() 기준으로 기록되므로 동일 존).
        LocalDateTime sinceLdt = LocalDateTime.ofInstant(since, ZoneId.systemDefault());
        List<String> upsert = changeLogRepository.findDistinctServiceIdsByChangedAtSinceAndTypeIn(
                sinceLdt, List.of(ChangeType.NEW, ChangeType.UPDATED));
        List<String> delete = changeLogRepository.findDistinctServiceIdsByChangedAtSinceAndTypeIn(
                sinceLdt, List.of(ChangeType.DELETED));
        return new ChangedServiceIds(upsert, delete);
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
