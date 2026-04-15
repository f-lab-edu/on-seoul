package dev.jazzybyte.onseoul.collector.repository;

import dev.jazzybyte.onseoul.collector.domain.DataSourceCatalog;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface DataSourceCatalogRepository extends JpaRepository<DataSourceCatalog, Long> {

    List<DataSourceCatalog> findAllByActiveTrue();
}
