package dev.jazzybyte.onseoul.user.application;

import dev.jazzybyte.onseoul.user.domain.User;
import dev.jazzybyte.onseoul.user.domain.UserStatus;
import dev.jazzybyte.onseoul.user.port.in.SocialLoginCommand;
import dev.jazzybyte.onseoul.user.port.in.TokenResponse;
import dev.jazzybyte.onseoul.user.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.user.port.out.RefreshTokenStorePort;
import dev.jazzybyte.onseoul.user.port.out.SaveUserPort;
import dev.jazzybyte.onseoul.user.port.out.TokenIssuerPort;
import dev.jazzybyte.onseoul.exception.EmailConflictException;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.BeforeEach;
import org.springframework.dao.DataIntegrityViolationException;
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
    @Mock private NewUserRegistrar newUserRegistrar;
    @Mock private TokenIssuerPort tokenIssuerPort;
    @Mock private RefreshTokenStorePort refreshTokenStorePort;

    private SocialLoginService service;

    @BeforeEach
    void setUp() {
        service = new SocialLoginService(
                loadUserPort, saveUserPort, newUserRegistrar, tokenIssuerPort, refreshTokenStorePort);
    }

    private SocialLoginCommand command(String provider, String providerId) {
        return new SocialLoginCommand(provider, providerId, "user@example.com", "홍길동");
    }

    private User activeUser(long id, String provider, String providerId) {
        return new User(id, provider, providerId, "old@example.com", "구닉네임",
                null, UserStatus.ACTIVE, OffsetDateTime.now(), OffsetDateTime.now());
    }

    private User userWithStatus(long id, UserStatus status) {
        return new User(id, "google", "provider-123", "user@example.com", "홍길동",
                null, status, OffsetDateTime.now(), OffsetDateTime.now());
    }

    @Test
    @DisplayName("신규 유저 — User.create()로 생성하고 저장한 뒤 토큰을 발급한다")
    void socialLogin_newUser_createsAndSavesUserAndIssuesTokens() {
        SocialLoginCommand cmd = command("google", "google-new-001");
        User savedUser = activeUser(10L, "google", "google-new-001");

        when(loadUserPort.findByProviderAndProviderId("google", "google-new-001"))
                .thenReturn(Optional.empty());
        when(newUserRegistrar.register(cmd)).thenReturn(savedUser);
        when(tokenIssuerPort.generateAccessToken(10L)).thenReturn("access-token");
        when(tokenIssuerPort.generateRefreshToken(10L)).thenReturn("refresh-token");
        when(tokenIssuerPort.getRefreshTokenMinutes()).thenReturn(10080L);

        TokenResponse result = service.socialLogin(cmd);

        assertThat(result.userId()).isEqualTo(10L);
        assertThat(result.accessToken()).isEqualTo("access-token");
        assertThat(result.refreshToken()).isEqualTo("refresh-token");

        verify(newUserRegistrar).register(cmd);
        verify(loadUserPort).findByProviderAndProviderId("google", "google-new-001");
    }

    @Test
    @DisplayName("신규 유저 — 신규 등록기에 전달되는 command는 전달된 provider/providerId를 갖는다")
    void socialLogin_newUser_savedUserHasCorrectFields() {
        SocialLoginCommand cmd = command("kakao", "kakao-999");
        User savedUser = activeUser(20L, "kakao", "kakao-999");

        when(loadUserPort.findByProviderAndProviderId("kakao", "kakao-999"))
                .thenReturn(Optional.empty());
        when(newUserRegistrar.register(any(SocialLoginCommand.class))).thenReturn(savedUser);
        when(tokenIssuerPort.generateAccessToken(anyLong())).thenReturn("at");
        when(tokenIssuerPort.generateRefreshToken(anyLong())).thenReturn("rt");
        when(tokenIssuerPort.getRefreshTokenMinutes()).thenReturn(10080L);

        service.socialLogin(cmd);

        ArgumentCaptor<SocialLoginCommand> cmdCaptor = ArgumentCaptor.forClass(SocialLoginCommand.class);
        verify(newUserRegistrar).register(cmdCaptor.capture());
        SocialLoginCommand captured = cmdCaptor.getValue();
        assertThat(captured.provider()).isEqualTo("kakao");
        assertThat(captured.providerId()).isEqualTo("kakao-999");
    }

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

        assertThat(existing.getEmail()).isEqualTo("new@example.com");
        assertThat(existing.getNickname()).isEqualTo("새닉네임");
        verify(saveUserPort).save(existing);
    }

    @Test
    @DisplayName("로그인 성공 시 RefreshToken을 ttlMinutes와 함께 Redis에 저장한다")
    void socialLogin_success_savesRefreshTokenWithTtl() {
        SocialLoginCommand cmd = command("google", "google-rt-001");
        User savedUser = activeUser(7L, "google", "google-rt-001");

        when(loadUserPort.findByProviderAndProviderId("google", "google-rt-001"))
                .thenReturn(Optional.empty());
        when(newUserRegistrar.register(cmd)).thenReturn(savedUser);
        when(tokenIssuerPort.generateAccessToken(7L)).thenReturn("at");
        when(tokenIssuerPort.generateRefreshToken(7L)).thenReturn("rt-value");
        when(tokenIssuerPort.getRefreshTokenMinutes()).thenReturn(10080L);

        service.socialLogin(cmd);

        verify(refreshTokenStorePort).save(7L, "rt-value", 10080L);
    }

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

        verifyNoInteractions(tokenIssuerPort, refreshTokenStorePort);
    }

    @Test
    @DisplayName("이메일 충돌 — 다른 벤더로 이미 가입된 이메일이면 EMAIL_ALREADY_REGISTERED를 던지고 저장하지 않는다")
    void socialLogin_emailRegisteredWithOtherProvider_throwsConflict() {
        SocialLoginCommand cmd = command("kakao", "kakao-conflict-001"); // email=user@example.com
        User existingGoogleUser = activeUser(99L, "google", "google-xyz");

        when(loadUserPort.findByProviderAndProviderId("kakao", "kakao-conflict-001"))
                .thenReturn(Optional.empty());
        when(loadUserPort.findByEmail("user@example.com"))
                .thenReturn(Optional.of(existingGoogleUser));

        assertThatThrownBy(() -> service.socialLogin(cmd))
                .isInstanceOf(EmailConflictException.class)
                .satisfies(ex -> {
                    EmailConflictException conflict = (EmailConflictException) ex;
                    assertThat(conflict.getErrorCode()).isEqualTo(ErrorCode.EMAIL_ALREADY_REGISTERED);
                    // provider는 메시지가 아니라 구조적 필드로 전달된다.
                    assertThat(conflict.getExistingProvider()).isEqualTo("google");
                });

        verify(newUserRegistrar, never()).register(any(SocialLoginCommand.class));
        verifyNoInteractions(tokenIssuerPort, refreshTokenStorePort);
    }

    @Test
    @DisplayName("이메일이 null인 카카오 신규 로그인 — 선검사를 건너뛰고 정상 가입한다")
    void socialLogin_nullEmail_skipsPrecheckAndCreates() {
        SocialLoginCommand cmd = new SocialLoginCommand("kakao", "kakao-noemail", null, "익명");
        User savedUser = activeUser(30L, "kakao", "kakao-noemail");

        when(loadUserPort.findByProviderAndProviderId("kakao", "kakao-noemail"))
                .thenReturn(Optional.empty());
        when(newUserRegistrar.register(cmd)).thenReturn(savedUser);
        when(tokenIssuerPort.generateAccessToken(30L)).thenReturn("at");
        when(tokenIssuerPort.generateRefreshToken(30L)).thenReturn("rt");
        when(tokenIssuerPort.getRefreshTokenMinutes()).thenReturn(10080L);

        service.socialLogin(cmd);

        verify(loadUserPort, never()).findByEmail(any());
        verify(newUserRegistrar).register(cmd);
    }

    @Test
    @DisplayName("경쟁 조건 — 신규 등록기의 DataIntegrityViolation을 provider 없는 EmailConflictException으로 변환한다")
    void socialLogin_dataIntegrityViolation_convertedToConflict() {
        SocialLoginCommand cmd = command("kakao", "kakao-race-001");

        when(loadUserPort.findByProviderAndProviderId("kakao", "kakao-race-001"))
                .thenReturn(Optional.empty());
        when(loadUserPort.findByEmail("user@example.com")).thenReturn(Optional.empty());
        when(newUserRegistrar.register(cmd))
                .thenThrow(new DataIntegrityViolationException("uq_users_email_hash"));

        assertThatThrownBy(() -> service.socialLogin(cmd))
                .isInstanceOf(EmailConflictException.class)
                .satisfies(ex -> {
                    EmailConflictException conflict = (EmailConflictException) ex;
                    assertThat(conflict.getErrorCode()).isEqualTo(ErrorCode.EMAIL_ALREADY_REGISTERED);
                    // 경쟁 조건에서는 기존 벤더를 알 수 없으므로 null.
                    assertThat(conflict.getExistingProvider()).isNull();
                });

        verifyNoInteractions(tokenIssuerPort, refreshTokenStorePort);
    }

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
