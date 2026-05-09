package dev.jazzybyte.onseoul.domain.port.out;

import dev.jazzybyte.onseoul.domain.model.User;

import java.util.Optional;

public interface LoadUserPort {
    Optional<User> findByProviderAndProviderId(String provider, String providerId);
    Optional<User> findById(Long id);
}
