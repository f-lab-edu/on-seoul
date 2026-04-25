package dev.jazzybyte.onseoul.adapter.out.persistence.catalog;

import dev.jazzybyte.onseoul.domain.model.ApiSourceCatalog;
import dev.jazzybyte.onseoul.domain.port.out.LoadApiSourceCatalogPort;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
class ApiSourceCatalogPersistenceAdapter implements LoadApiSourceCatalogPort {

    private final ApiSourceCatalogJpaRepository jpaRepository;

    ApiSourceCatalogPersistenceAdapter(final ApiSourceCatalogJpaRepository jpaRepository) {
        this.jpaRepository = jpaRepository;
    }

    @Override
    public List<ApiSourceCatalog> findAllByActiveTrue() {
        return jpaRepository.findAllByActiveTrue().stream()
                .map(this::toDomain)
                .toList();
    }

    private ApiSourceCatalog toDomain(ApiSourceCatalogJpaEntity e) {
        return ApiSourceCatalog.builder()
                .id(e.getId())
                .datasetId(e.getDatasetId())
                .datasetName(e.getDatasetName())
                .datasetUrl(e.getDatasetUrl())
                .apiServicePath(e.getApiServicePath())
                .active(e.isActive())
                .tags(e.getTags())
                .metaUpdatedAt(e.getMetaUpdatedAt())
                .dataUpdatedAt(e.getDataUpdatedAt())
                .createdAt(e.getCreatedAt())
                .build();
    }
}
