package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;

import java.util.Optional;

public interface SaveDispatchPort {

    /**
     * Inserts the dispatch only if no row with the same (subscription_id, change_log_id) exists.
     * Returns the saved entity, or empty if a duplicate already exists (ON CONFLICT DO NOTHING semantics).
     */
    Optional<NotificationDispatch> saveIfAbsent(NotificationDispatch dispatch);

    NotificationDispatch save(NotificationDispatch dispatch);
}
