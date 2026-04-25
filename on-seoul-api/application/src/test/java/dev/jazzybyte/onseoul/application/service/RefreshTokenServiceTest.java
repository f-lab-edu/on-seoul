package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.model.User;
import dev.jazzybyte.onseoul.domain.model.UserStatus;
import dev.jazzybyte.onseoul.domain.port.in.TokenResponse;
import dev.jazzybyte.onseoul.domain.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.domain.port.out.RefreshTokenStorePort;
import dev.jazzybyte.onseoul.domain.port.out.TokenIssuerPort;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.OffsetDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class RefreshTokenServiceTest {

    @Mock private TokenIssuerPort tokenIssuerPort;
    @Mock private RefreshTokenStorePort refreshTokenStorePort;
    @Mock private LoadUserPort loadUserPort;

    private RefreshTokenService service;

    @BeforeEach
    void setUp() {
        service = new RefreshTokenService(tokenIssuerPort, refreshTokenStorePort, loadUserPort);
    }

    private User activeUser(long id) {
        return new User(id, "google", "provider-" + id, "test@test.com", "tester",
                UserStatus.ACTIVE, OffsetDateTime.now(), OffsetDateTime.now());
    }

    @Test
    @DisplayName("유효한 Refresh Token이면 새 Access Token과 새 Refresh Token을 반환한다(Token Rotation)")
    void refresh_validToken_returnsNewTokenPair() {
        String refreshToken = "valid-refresh-token";
        long userId = 42L;

        doNothing().when(tokenIssuerPort).validateToken(refreshToken);
        when(tokenIssuerPort.extractUserId(refreshToken)).thenReturn(userId);
        when(refreshTokenStorePort.find(userId)).thenReturn(Optional.of(refreshToken));
        when(loadUserPort.findById(userId)).thenReturn(Optional.of(activeUser(userId)));
        when(tokenIssuerPort.generateAccessToken(userId)).thenReturn("new-access-token");
        when(tokenIssuerPort.generateRefreshToken(userId)).thenReturn("new-refresh-token");

        TokenResponse result = service.refresh(refreshToken);

        assertThat(result.accessToken()).isEqualTo("new-access-token");
        assertThat(result.refreshToken()).isEqualTo("new-refresh-token");
        verify(refreshTokenStorePort).delete(userId);
        verify(refreshTokenStorePort).save(userId, "new-refresh-token");
    }

    @Test
    @DisplayName("Redis에 Refresh Token이 없으면 OnSeoulApiException을 던진다")
    void refresh_tokenNotInStore_throwsException() {
        String refreshToken = "valid-refresh-token";
        long userId = 42L;

        doNothing().when(tokenIssuerPort).validateToken(refreshToken);
        when(tokenIssuerPort.extractUserId(refreshToken)).thenReturn(userId);
        when(refreshTokenStorePort.find(userId)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.refresh(refreshToken))
                .isInstanceOf(OnSeoulApiException.class);
    }

    @Test
    @DisplayName("저장된 토큰과 다르면 OnSeoulApiException을 던진다")
    void refresh_tokenMismatch_throwsException() {
        String refreshToken = "valid-refresh-token";
        long userId = 42L;

        doNothing().when(tokenIssuerPort).validateToken(refreshToken);
        when(tokenIssuerPort.extractUserId(refreshToken)).thenReturn(userId);
        when(refreshTokenStorePort.find(userId)).thenReturn(Optional.of("different-token"));

        assertThatThrownBy(() -> service.refresh(refreshToken))
                .isInstanceOf(OnSeoulApiException.class);
    }

    @Test
    @DisplayName("SUSPENDED 사용자는 refresh 시 FORBIDDEN 예외를 던진다")
    void refresh_suspendedUser_throwsForbidden() {
        String refreshToken = "valid-refresh-token";
        long userId = 42L;
        User suspended = new User(userId, "google", "p", "e", "n",
                UserStatus.SUSPENDED, OffsetDateTime.now(), OffsetDateTime.now());

        doNothing().when(tokenIssuerPort).validateToken(refreshToken);
        when(tokenIssuerPort.extractUserId(refreshToken)).thenReturn(userId);
        when(refreshTokenStorePort.find(userId)).thenReturn(Optional.of(refreshToken));
        when(loadUserPort.findById(userId)).thenReturn(Optional.of(suspended));

        assertThatThrownBy(() -> service.refresh(refreshToken))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode().getHttpStatus()).isEqualTo(403));
    }

    @Test
    @DisplayName("로그아웃 시 RefreshTokenStore에서 삭제한다")
    void logout_deletesToken() {
        LogoutService logoutService = new LogoutService(refreshTokenStorePort);
        logoutService.logout(42L);
        verify(refreshTokenStorePort).delete(42L);
    }
}
