package dev.jazzybyte.onseoul.collection.port.out;

import dev.jazzybyte.onseoul.collection.domain.CollectionHistory;

public interface SaveCollectionHistoryPort {
    CollectionHistory save(CollectionHistory history);
}
