package dev.jazzybyte.onseoul.domain.port.out;

import java.util.Optional;

public interface RefreshTokenStorePort {
    void save(Long userId, String refreshToken, long ttlMinutes);
    Optional<String> getAndDelete(Long userId);   // 원자적 GET+DELETE (Token Rotation)
    void delete(Long userId);                     // 로그아웃 전용 (값 없어도 ok)
}
