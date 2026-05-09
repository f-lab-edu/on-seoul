package dev.jazzybyte.onseoul.security.jwt;

import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class JwtProviderTest {

    private JwtProvider jwtProvider;

    @BeforeEach
    void setUp() {
        // 256-bit base64 secret for HS256
        String secret = "dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=";
        jwtProvider = new JwtProvider(secret, 15L, 60L * 24L * 7L);
    }

    @Test
    @DisplayName("Access Token을 생성하면 subject로 userId를 담고 있다")
    void generateAccessToken_containsUserId() {
        String token = jwtProvider.generateAccessToken(42L);

        Long userId = jwtProvider.extractUserId(token);

        assertThat(userId).isEqualTo(42L);
    }

    @Test
    @DisplayName("유효한 Access Token 검증 시 true를 반환한다")
    void validateToken_validToken_returnsTrue() {
        String token = jwtProvider.generateAccessToken(1L);

        assertThat(jwtProvider.validateToken(token)).isTrue();
    }

    @Test
    @DisplayName("만료된 토큰 검증 시 OnSeoulApiException(EXPIRED_TOKEN)을 던진다")
    void validateToken_expiredToken_throwsException() {
        // TTL을 -1분으로 설정하면 즉시 만료
        JwtProvider expiredProvider = new JwtProvider(
                "dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=",
                -1L,
                -1L
        );
        String token = expiredProvider.generateAccessToken(1L);

        assertThatThrownBy(() -> expiredProvider.validateToken(token))
                .isInstanceOf(OnSeoulApiException.class)
                .hasMessageContaining("만료");
    }

    @Test
    @DisplayName("변조된 토큰 검증 시 OnSeoulApiException(INVALID_TOKEN)을 던진다")
    void validateToken_tamperedToken_throwsException() {
        String token = jwtProvider.generateAccessToken(1L) + "tampered";

        assertThatThrownBy(() -> jwtProvider.validateToken(token))
                .isInstanceOf(OnSeoulApiException.class);
    }

    @Test
    @DisplayName("Refresh Token을 생성하면 subject로 userId를 담고 있다")
    void generateRefreshToken_containsUserId() {
        String token = jwtProvider.generateRefreshToken(99L);

        Long userId = jwtProvider.extractUserId(token);

        assertThat(userId).isEqualTo(99L);
    }

    @Test
    @DisplayName("Access Token은 extractUserIdSafely에서 userId를 반환한다")
    void extractUserIdSafely_accessToken_returnsUserId() {
        String token = jwtProvider.generateAccessToken(42L);

        assertThat(jwtProvider.extractUserIdSafely(token)).contains(42L);
    }

    @Test
    @DisplayName("Refresh Token은 extractUserIdSafely에서 empty를 반환한다 — 필터 오용 방지")
    void extractUserIdSafely_refreshToken_returnsEmpty() {
        String refreshToken = jwtProvider.generateRefreshToken(42L);

        assertThat(jwtProvider.extractUserIdSafely(refreshToken)).isEmpty();
    }

    @Test
    @DisplayName("Refresh Token은 extractUserIdFromRefreshToken에서 userId를 반환한다")
    void extractUserIdFromRefreshToken_validRefreshToken_returnsUserId() {
        String refreshToken = jwtProvider.generateRefreshToken(99L);

        assertThat(jwtProvider.extractUserIdFromRefreshToken(refreshToken)).isEqualTo(99L);
    }

    @Test
    @DisplayName("Access Token을 extractUserIdFromRefreshToken에 전달하면 INVALID_TOKEN 예외가 발생한다")
    void extractUserIdFromRefreshToken_accessToken_throwsInvalidToken() {
        String accessToken = jwtProvider.generateAccessToken(99L);

        assertThatThrownBy(() -> jwtProvider.extractUserIdFromRefreshToken(accessToken))
                .isInstanceOf(OnSeoulApiException.class)
                .hasMessageContaining("Refresh Token이 아닙니다");
    }
}
