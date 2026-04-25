package dev.jazzybyte.onseoul.domain.port.out;

import dev.jazzybyte.onseoul.domain.model.ServiceChangeLog;

import java.util.List;

public interface SaveServiceChangeLogPort {
    void saveAll(List<ServiceChangeLog> logs);
}
