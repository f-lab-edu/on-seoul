package dev.jazzybyte.onseoul.adapter.out.persistence.catalog;

import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

interface ApiSourceCatalogJpaRepository extends JpaRepository<ApiSourceCatalogJpaEntity, Long> {
    List<ApiSourceCatalogJpaEntity> findAllByActiveTrue();
}
