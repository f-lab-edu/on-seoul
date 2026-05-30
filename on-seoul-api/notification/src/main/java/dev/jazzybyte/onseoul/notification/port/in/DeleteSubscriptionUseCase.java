package dev.jazzybyte.onseoul.notification.port.in;

public interface DeleteSubscriptionUseCase {

    void delete(Long userId, Long subscriptionId);
}
