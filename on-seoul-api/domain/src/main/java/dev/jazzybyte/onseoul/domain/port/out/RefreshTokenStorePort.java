package dev.jazzybyte.onseoul.domain.port.out;

import java.util.Optional;

public interface RefreshTokenStorePort {
    void save(Long userId, String refreshToken);
    Optional<String> find(Long userId);
    void delete(Long userId);
}
