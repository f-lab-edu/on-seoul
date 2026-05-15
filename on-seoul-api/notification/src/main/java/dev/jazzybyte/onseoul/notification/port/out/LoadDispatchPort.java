package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;

import java.util.Optional;

public interface LoadDispatchPort {

    /**
     * Loads a retryable dispatch for the given subscription and change-log pair,
     * i.e. one whose status is PENDING or FAILED and whose attempt count is below maxAttempts.
     */
    Optional<NotificationDispatch> loadRetryable(Long subscriptionId, Long changeLogId, int maxAttempts);
}
