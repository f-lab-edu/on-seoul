package dev.jazzybyte.onseoul.domain.port.out;

import java.util.Optional;

public interface TokenIssuerPort {
    String generateAccessToken(long userId);
    String generateRefreshToken(long userId);
    void validateToken(String token);
    Long extractUserId(String token);
    Optional<Long> extractUserIdSafely(String token);
    long getAccessTokenMinutes();
    long getRefreshTokenMinutes();
}
