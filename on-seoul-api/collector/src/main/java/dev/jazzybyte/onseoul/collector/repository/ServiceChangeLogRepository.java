package dev.jazzybyte.onseoul.collector.repository;

import dev.jazzybyte.onseoul.collector.domain.ServiceChangeLog;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ServiceChangeLogRepository extends JpaRepository<ServiceChangeLog, Long> {

    List<ServiceChangeLog> findAllByCollectionId(Long collectionId);

    List<ServiceChangeLog> findAllByServiceIdOrderByChangedAtDesc(String serviceId);
}
