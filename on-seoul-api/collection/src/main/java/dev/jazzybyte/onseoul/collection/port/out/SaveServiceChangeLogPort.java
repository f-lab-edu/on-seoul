package dev.jazzybyte.onseoul.collection.port.out;

import dev.jazzybyte.onseoul.collection.domain.ServiceChangeLog;

import java.util.List;

public interface SaveServiceChangeLogPort {
    void saveAll(List<ServiceChangeLog> logs);
}
