package dev.jazzybyte.onseoul.adapter.in.security;

import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class JjwtTokenIssuerTest {

    private JjwtTokenIssuer tokenIssuer;

    @BeforeEach
    void setUp() {
        String secret = "dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=";
        tokenIssuer = new JjwtTokenIssuer(secret, 15L, 60L * 24L * 7L);
    }

    @Test
    @DisplayName("Access Token을 생성하면 subject로 userId를 담고 있다")
    void generateAccessToken_containsUserId() {
        String token = tokenIssuer.generateAccessToken(42L);
        assertThat(tokenIssuer.extractUserId(token)).isEqualTo(42L);
    }

    @Test
    @DisplayName("유효한 Access Token 검증 시 예외가 발생하지 않는다")
    void validateToken_validToken_doesNotThrow() {
        String token = tokenIssuer.generateAccessToken(1L);
        tokenIssuer.validateToken(token);
    }

    @Test
    @DisplayName("만료된 토큰 검증 시 OnSeoulApiException(EXPIRED_TOKEN)을 던진다")
    void validateToken_expiredToken_throwsException() {
        JjwtTokenIssuer expiredIssuer = new JjwtTokenIssuer(
                "dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=",
                -1L, -1L);
        String token = expiredIssuer.generateAccessToken(1L);

        assertThatThrownBy(() -> expiredIssuer.validateToken(token))
                .isInstanceOf(OnSeoulApiException.class)
                .hasMessageContaining("만료");
    }

    @Test
    @DisplayName("변조된 토큰 검증 시 OnSeoulApiException을 던진다")
    void validateToken_tamperedToken_throwsException() {
        String token = tokenIssuer.generateAccessToken(1L) + "tampered";
        assertThatThrownBy(() -> tokenIssuer.validateToken(token))
                .isInstanceOf(OnSeoulApiException.class);
    }

    @Test
    @DisplayName("Refresh Token을 생성하면 subject로 userId를 담고 있다")
    void generateRefreshToken_containsUserId() {
        String token = tokenIssuer.generateRefreshToken(99L);
        assertThat(tokenIssuer.extractUserId(token)).isEqualTo(99L);
    }

    @Test
    @DisplayName("유효하지 않은 토큰은 extractUserIdSafely가 empty를 반환한다")
    void extractUserIdSafely_invalidToken_returnsEmpty() {
        assertThat(tokenIssuer.extractUserIdSafely("invalid.token.here")).isEmpty();
    }
}
