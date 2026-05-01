package dev.jazzybyte.onseoul.auth;

import dev.jazzybyte.onseoul.auth.dto.TokenResponse;
import dev.jazzybyte.onseoul.domain.User;
import dev.jazzybyte.onseoul.domain.UserStatus;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.repository.UserRepository;
import dev.jazzybyte.onseoul.security.jwt.JwtProvider;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.util.Optional;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class AuthServiceTest {

    @Mock
    private StringRedisTemplate redisTemplate;

    @Mock
    private ValueOperations<String, String> valueOperations;

    @Mock
    private UserRepository userRepository;

    private JwtProvider jwtProvider;
    private AuthService authService;

    @BeforeEach
    void setUp() {
        String secret = "dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=";
        jwtProvider = new JwtProvider(secret, 15L, 60L * 24L * 7L);
        authService = new AuthService(jwtProvider, redisTemplate, userRepository);
    }

    private User activeUser(long userId) {
        // Use reflection-free approach: build through the public builder (status defaults to ACTIVE)
        return User.builder()
                .provider("google")
                .providerId("provider-" + userId)
                .email("user@test.com")
                .nickname("tester")
                .build();
    }

    @Test
    @DisplayName("유효한 Refresh Token이면 새 Access Token과 새 Refresh Token을 반환한다(Token Rotation)")
    void refresh_validToken_returnsNewTokenPair() {
        long userId = 42L;
        String refreshToken = jwtProvider.generateRefreshToken(userId);
        String redisKey = "RT:" + userId;

        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.getAndDelete(redisKey)).thenReturn(refreshToken);
        when(userRepository.findById(userId)).thenReturn(Optional.of(activeUser(userId)));

        TokenResponse result = authService.refresh(refreshToken);

        assertThat(result.accessToken()).isNotBlank();
        assertThat(result.refreshToken()).isNotBlank();
        assertThat(jwtProvider.extractUserId(result.accessToken())).isEqualTo(userId);
        assertThat(jwtProvider.extractUserId(result.refreshToken())).isEqualTo(userId);
        // getAndDelete로 원자적 삭제됨 — 별도 delete 호출 없음
        verify(redisTemplate, never()).delete(anyString());
        // Redis TTL = jwt.refresh-token-minutes (JwtProvider 단일 소스)
        verify(valueOperations).set(eq(redisKey), anyString(), eq(10080L), eq(TimeUnit.MINUTES));
    }

    @Test
    @DisplayName("새로 발급된 Refresh Token은 기존 Refresh Token과 달라야 한다(Rotation)")
    void refresh_rotation_newRefreshTokenDiffersFromOld() {
        long userId = 42L;
        String oldRefreshToken = jwtProvider.generateRefreshToken(userId);
        String redisKey = "RT:" + userId;

        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.getAndDelete(redisKey)).thenReturn(oldRefreshToken);
        when(userRepository.findById(userId)).thenReturn(Optional.of(activeUser(userId)));

        authService.refresh(oldRefreshToken);

        // getAndDelete가 원자적으로 기존 토큰을 소비함 — 별도 delete 호출 없음
        verify(redisTemplate, never()).delete(anyString());
        verify(valueOperations).getAndDelete(redisKey);
    }

    @Test
    @DisplayName("Redis에 Refresh Token이 없으면(로그아웃 후) OnSeoulApiException을 던진다")
    void refresh_tokenNotInRedis_throwsException() {
        long userId = 42L;
        String refreshToken = jwtProvider.generateRefreshToken(userId);
        String redisKey = "RT:" + userId;

        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.getAndDelete(redisKey)).thenReturn(null);

        assertThatThrownBy(() -> authService.refresh(refreshToken))
                .isInstanceOf(OnSeoulApiException.class);
    }

    @Test
    @DisplayName("Redis에 저장된 토큰과 다르면 OnSeoulApiException을 던진다")
    void refresh_tokenMismatch_throwsException() {
        long userId = 42L;
        String refreshToken = jwtProvider.generateRefreshToken(userId);
        String differentToken = jwtProvider.generateRefreshToken(999L);
        String redisKey = "RT:" + userId;

        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        // getAndDelete: 꺼낸 값이 달라도 키는 이미 삭제됨 — 재사용 불가
        when(valueOperations.getAndDelete(redisKey)).thenReturn(differentToken);

        assertThatThrownBy(() -> authService.refresh(refreshToken))
                .isInstanceOf(OnSeoulApiException.class);
    }

    @Test
    @DisplayName("SUSPENDED 사용자는 refresh 시 FORBIDDEN 예외를 던진다")
    void refresh_suspendedUser_throwsForbidden() {
        long userId = 42L;
        String refreshToken = jwtProvider.generateRefreshToken(userId);
        String redisKey = "RT:" + userId;

        User suspendedUser = mock(User.class);
        when(suspendedUser.getStatus()).thenReturn(UserStatus.SUSPENDED);

        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.getAndDelete(redisKey)).thenReturn(refreshToken);
        when(userRepository.findById(userId)).thenReturn(Optional.of(suspendedUser));

        assertThatThrownBy(() -> authService.refresh(refreshToken))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode().getHttpStatus()).isEqualTo(403));
    }

    @Test
    @DisplayName("DELETED 사용자는 refresh 시 FORBIDDEN 예외를 던진다")
    void refresh_deletedUser_throwsForbidden() {
        long userId = 42L;
        String refreshToken = jwtProvider.generateRefreshToken(userId);
        String redisKey = "RT:" + userId;

        User deletedUser = mock(User.class);
        when(deletedUser.getStatus()).thenReturn(UserStatus.DELETED);

        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.getAndDelete(redisKey)).thenReturn(refreshToken);
        when(userRepository.findById(userId)).thenReturn(Optional.of(deletedUser));

        assertThatThrownBy(() -> authService.refresh(refreshToken))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode().getHttpStatus()).isEqualTo(403));
    }

    @Test
    @DisplayName("로그아웃 시 Redis에서 Refresh Token을 삭제한다")
    void logout_deletesRefreshTokenFromRedis() {
        long userId = 42L;
        String redisKey = "RT:" + userId;

        authService.logout(userId);

        verify(redisTemplate).delete(redisKey);
    }

    @Test
    @DisplayName("만료된 Refresh Token으로 refresh 호출 시 OnSeoulApiException(EXPIRED_TOKEN)을 던진다")
    void refresh_expiredToken_throwsExpiredTokenException() {
        JwtProvider expiredProvider = new JwtProvider(
                "dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=",
                -1L, -1L);
        AuthService serviceWithExpiredProvider = new AuthService(expiredProvider, redisTemplate, userRepository);
        String expiredRefreshToken = expiredProvider.generateRefreshToken(1L);

        assertThatThrownBy(() -> serviceWithExpiredProvider.refresh(expiredRefreshToken))
                .isInstanceOf(OnSeoulApiException.class)
                .hasMessageContaining("만료");

        verify(redisTemplate, never()).opsForValue();
    }

    @Test
    @DisplayName("변조된 Refresh Token으로 refresh 호출 시 OnSeoulApiException(INVALID_TOKEN)을 던진다")
    void refresh_tamperedToken_throwsInvalidTokenException() {
        String tamperedToken = jwtProvider.generateRefreshToken(1L) + "tampered";

        assertThatThrownBy(() -> authService.refresh(tamperedToken))
                .isInstanceOf(OnSeoulApiException.class);

        verify(redisTemplate, never()).opsForValue();
    }

    @Test
    @DisplayName("Access Token을 Refresh Token 자리에 전달하면 Redis 조회 없이 INVALID_TOKEN 예외를 던진다")
    void refresh_accessTokenPassedAsRefresh_throwsBeforeRedis() {
        String accessToken = jwtProvider.generateAccessToken(42L);

        assertThatThrownBy(() -> authService.refresh(accessToken))
                .isInstanceOf(OnSeoulApiException.class)
                .hasMessageContaining("Refresh Token이 아닙니다");

        verify(redisTemplate, never()).opsForValue();
    }
}
