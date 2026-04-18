package dev.jazzybyte.onseoul.collector.repository;

import dev.jazzybyte.onseoul.collector.domain.ApiSourceCatalog;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ApiSourceCatalogRepository extends JpaRepository<ApiSourceCatalog, Long> {

    List<ApiSourceCatalog> findAllByActiveTrue();
}
