package dev.jazzybyte.onseoul.notification.port.in;

public interface CreateDefaultSubscriptionsUseCase {

    /**
     * Creates default notification subscriptions for a newly registered user.
     *
     * @param userId the ID of the user
     */
    void create(Long userId);
}
