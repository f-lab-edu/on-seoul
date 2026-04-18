package dev.jazzybyte.onseoul.collector.repository;

import dev.jazzybyte.onseoul.collector.domain.CollectionHistory;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface CollectionHistoryRepository extends JpaRepository<CollectionHistory, Long> {

    List<CollectionHistory> findAllBySourceIdOrderByCollectedAtDesc(Long sourceId);
}
