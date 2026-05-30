package dev.jazzybyte.onseoul.user.port.out;

import dev.jazzybyte.onseoul.user.domain.User;

import java.util.Optional;

public interface LoadUserPort {
    Optional<User> findByProviderAndProviderId(String provider, String providerId);
    Optional<User> findById(Long id);
}
