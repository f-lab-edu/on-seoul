package dev.jazzybyte.onseoul.domain.port.out;

import dev.jazzybyte.onseoul.domain.model.CollectionHistory;

public interface SaveCollectionHistoryPort {
    CollectionHistory save(CollectionHistory history);
}
