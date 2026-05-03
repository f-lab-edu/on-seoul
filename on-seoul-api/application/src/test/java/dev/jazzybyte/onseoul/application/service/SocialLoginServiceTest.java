package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.model.User;
import dev.jazzybyte.onseoul.domain.model.UserStatus;
import dev.jazzybyte.onseoul.domain.port.in.SocialLoginCommand;
import dev.jazzybyte.onseoul.domain.port.in.TokenResponse;
import dev.jazzybyte.onseoul.domain.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.domain.port.out.RefreshTokenStorePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveUserPort;
import dev.jazzybyte.onseoul.domain.port.out.TokenIssuerPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.OffsetDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class SocialLoginServiceTest {

    @Mock private LoadUserPort loadUserPort;
    @Mock private SaveUserPort saveUserPort;
    @Mock private TokenIssuerPort tokenIssuerPort;
    @Mock private RefreshTokenStorePort refreshTokenStorePort;

    private SocialLoginService service;

    @BeforeEach
    void setUp() {
        service = new SocialLoginService(loadUserPort, saveUserPort, tokenIssuerPort, refreshTokenStorePort);
    }

    // ── 헬퍼 ─────────────────────────────────────────────────────────

    private SocialLoginCommand command(String provider, String providerId) {
        return new SocialLoginCommand(provider, providerId, "user@example.com", "홍길동");
    }

    private User activeUser(long id, String provider, String providerId) {
        return new User(id, provider, providerId, "old@example.com", "구닉네임",
                UserStatus.ACTIVE, OffsetDateTime.now(), OffsetDateTime.now());
    }

    private User userWithStatus(long id, UserStatus status) {
        return new User(id, "google", "provider-123", "user@example.com", "홍길동",
                status, OffsetDateTime.now(), OffsetDateTime.now());
    }

    // ── 신규 유저 ─────────────────────────────────────────────────────

    @Test
    @DisplayName("신규 유저 — User.create()로 생성하고 저장한 뒤 토큰을 발급한다")
    void socialLogin_newUser_createsAndSavesUserAndIssuesTokens() {
        SocialLoginCommand cmd = command("google", "google-new-001");
        User savedUser = activeUser(10L, "google", "google-new-001");

        when(loadUserPort.findByProviderAndProviderId("google", "google-new-001"))
                .thenReturn(Optional.empty());
        when(saveUserPort.save(any(User.class))).thenReturn(savedUser);
        when(tokenIssuerPort.generateAccessToken(10L)).thenReturn("access-token");
        when(tokenIssuerPort.generateRefreshToken(10L)).thenReturn("refresh-token");
        when(tokenIssuerPort.getRefreshTokenMinutes()).thenReturn(10080L);

        TokenResponse result = service.socialLogin(cmd);

        assertThat(result.accessToken()).isEqualTo("access-token");
        assertThat(result.refreshToken()).isEqualTo("refresh-token");

        // saveUserPort.save()가 1회 호출됨
        verify(saveUserPort).save(any(User.class));
        // 기존 유저 수정 경로는 타지 않음
        verify(loadUserPort).findByProviderAndProviderId("google", "google-new-001");
    }

    @Test
    @DisplayName("신규 유저 — 저장된 User 객체는 ACTIVE 상태, 전달된 provider/providerId를 갖는다")
    void socialLogin_newUser_savedUserHasCorrectFields() {
        SocialLoginCommand cmd = command("kakao", "kakao-999");
        User savedUser = activeUser(20L, "kakao", "kakao-999");

        when(loadUserPort.findByProviderAndProviderId("kakao", "kakao-999"))
                .thenReturn(Optional.empty());
        when(saveUserPort.save(any(User.class))).thenReturn(savedUser);
        when(tokenIssuerPort.generateAccessToken(anyLong())).thenReturn("at");
        when(tokenIssuerPort.generateRefreshToken(anyLong())).thenReturn("rt");
        when(tokenIssuerPort.getRefreshTokenMinutes()).thenReturn(10080L);

        service.socialLogin(cmd);

        ArgumentCaptor<User> userCaptor = ArgumentCaptor.forClass(User.class);
        verify(saveUserPort).save(userCaptor.capture());
        User capturedUser = userCaptor.getValue();
        assertThat(capturedUser.getProvider()).isEqualTo("kakao");
        assertThat(capturedUser.getProviderId()).isEqualTo("kakao-999");
        assertThat(capturedUser.getStatus()).isEqualTo(UserStatus.ACTIVE);
    }

    // ── 기존 유저 ─────────────────────────────────────────────────────

    @Test
    @DisplayName("기존 유저 — updateProfile()을 호출하고 save한 뒤 토큰을 발급한다")
    void socialLogin_existingUser_updatesProfileAndIssuesTokens() {
        SocialLoginCommand cmd = new SocialLoginCommand("google", "google-existing-001",
                "new@example.com", "새닉네임");
        User existing = activeUser(5L, "google", "google-existing-001");

        when(loadUserPort.findByProviderAndProviderId("google", "google-existing-001"))
                .thenReturn(Optional.of(existing));
        when(saveUserPort.save(existing)).thenReturn(existing);
        when(tokenIssuerPort.generateAccessToken(5L)).thenReturn("access");
        when(tokenIssuerPort.generateRefreshToken(5L)).thenReturn("refresh");
        when(tokenIssuerPort.getRefreshTokenMinutes()).thenReturn(10080L);

        service.socialLogin(cmd);

        // updateProfile이 새 이메일/닉네임으로 적용됐는지 검증
        assertThat(existing.getEmail()).isEqualTo("new@example.com");
        assertThat(existing.getNickname()).isEqualTo("새닉네임");
        verify(saveUserPort).save(existing);
    }

    // ── Refresh Token 저장 ────────────────────────────────────────────

    @Test
    @DisplayName("로그인 성공 시 RefreshToken을 ttlMinutes와 함께 Redis에 저장한다")
    void socialLogin_success_savesRefreshTokenWithTtl() {
        SocialLoginCommand cmd = command("google", "google-rt-001");
        User savedUser = activeUser(7L, "google", "google-rt-001");

        when(loadUserPort.findByProviderAndProviderId("google", "google-rt-001"))
                .thenReturn(Optional.empty());
        when(saveUserPort.save(any())).thenReturn(savedUser);
        when(tokenIssuerPort.generateAccessToken(7L)).thenReturn("at");
        when(tokenIssuerPort.generateRefreshToken(7L)).thenReturn("rt-value");
        when(tokenIssuerPort.getRefreshTokenMinutes()).thenReturn(10080L);

        service.socialLogin(cmd);

        verify(refreshTokenStorePort).save(7L, "rt-value", 10080L);
    }

    // ── SUSPENDED 차단 ────────────────────────────────────────────────

    @Test
    @DisplayName("SUSPENDED 유저는 FORBIDDEN 예외를 발생시킨다")
    void socialLogin_suspendedUser_throwsForbidden() {
        SocialLoginCommand cmd = command("google", "google-suspended");
        User suspended = userWithStatus(3L, UserStatus.SUSPENDED);

        when(loadUserPort.findByProviderAndProviderId("google", "google-suspended"))
                .thenReturn(Optional.of(suspended));
        when(saveUserPort.save(suspended)).thenReturn(suspended);

        assertThatThrownBy(() -> service.socialLogin(cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> {
                    OnSeoulApiException apiEx = (OnSeoulApiException) ex;
                    assertThat(apiEx.getErrorCode()).isEqualTo(ErrorCode.FORBIDDEN);
                    assertThat(apiEx.getMessage()).contains("비활성화된 계정");
                });

        // 토큰 발급, Redis 저장 없음
        verifyNoInteractions(tokenIssuerPort, refreshTokenStorePort);
    }

    // ── DELETED 차단 ──────────────────────────────────────────────────

    @Test
    @DisplayName("DELETED 유저는 FORBIDDEN 예외를 발생시킨다")
    void socialLogin_deletedUser_throwsForbidden() {
        SocialLoginCommand cmd = command("google", "google-deleted");
        User deleted = userWithStatus(4L, UserStatus.DELETED);

        when(loadUserPort.findByProviderAndProviderId("google", "google-deleted"))
                .thenReturn(Optional.of(deleted));
        when(saveUserPort.save(deleted)).thenReturn(deleted);

        assertThatThrownBy(() -> service.socialLogin(cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex ->
                        assertThat(((OnSeoulApiException) ex).getErrorCode()).isEqualTo(ErrorCode.FORBIDDEN));
    }
}
