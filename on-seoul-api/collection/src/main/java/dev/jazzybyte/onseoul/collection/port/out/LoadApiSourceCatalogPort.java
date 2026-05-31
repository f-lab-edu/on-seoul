package dev.jazzybyte.onseoul.collection.port.out;

import dev.jazzybyte.onseoul.collection.domain.ApiSourceCatalog;

import java.util.List;

public interface LoadApiSourceCatalogPort {
    List<ApiSourceCatalog> findAllByActiveTrue();
}
