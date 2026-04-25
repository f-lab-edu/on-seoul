package dev.jazzybyte.onseoul.domain.port.out;

import dev.jazzybyte.onseoul.domain.model.ApiSourceCatalog;

import java.util.List;

public interface LoadApiSourceCatalogPort {
    List<ApiSourceCatalog> findAllByActiveTrue();
}
