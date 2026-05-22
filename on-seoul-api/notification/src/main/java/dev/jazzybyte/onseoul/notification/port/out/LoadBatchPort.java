package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;

import java.util.Optional;

public interface LoadBatchPort {

    Optional<NotificationBatch> loadById(Long batchId);
}
