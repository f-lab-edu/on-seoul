package dev.jazzybyte.onseoul.auth;

import dev.jazzybyte.onseoul.auth.dto.TokenResponse;
import dev.jazzybyte.onseoul.domain.User;
import dev.jazzybyte.onseoul.domain.UserStatus;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.repository.UserRepository;
import dev.jazzybyte.onseoul.security.jwt.JwtProvider;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.util.concurrent.TimeUnit;

@Service
public class AuthService {

    private final JwtProvider jwtProvider;
    private final StringRedisTemplate redisTemplate;
    private final UserRepository userRepository;

    public AuthService(final JwtProvider jwtProvider,
                       final StringRedisTemplate redisTemplate,
                       final UserRepository userRepository) {
        this.jwtProvider = jwtProvider;
        this.redisTemplate = redisTemplate;
        this.userRepository = userRepository;
    }

    /**
     * Refresh Token을 검증하고 새 Access Token과 새 Refresh Token을 발급한다(Token Rotation).
     *
     * <ol>
     *   <li>JWT type 클레임 검증 — refresh 타입이 아니면 즉시 거부</li>
     *   <li>Redis에서 {@code RT:{userId}}를 <b>원자적으로(getAndDelete)</b> 꺼냄
     *       — get + delete를 두 번 호출하면 동시 요청이 모두 유효 판정을 받을 수 있으므로(TOCTOU),
     *         단일 연산으로 조회와 삭제를 처리한다</li>
     *   <li>꺼낸 값이 없거나 제출된 토큰과 다르면 예외 — 이미 삭제되었으므로 재사용 불가</li>
     *   <li>사용자 status 확인 — ACTIVE가 아니면 FORBIDDEN 예외</li>
     *   <li>새 토큰 쌍 발급 후 새 Refresh Token을 Redis에 저장</li>
     * </ol>
     *
     * <p><b>Redis TTL</b>은 {@link JwtProvider#getRefreshTokenMinutes()}에서 파생되어
     * JWT 만료 시간과 항상 동기화된다.</p>
     *
     * @param refreshToken 클라이언트가 제출한 Refresh Token
     * @return 새로 발급된 AccessToken + RefreshToken 쌍
     */
    public TokenResponse refresh(String refreshToken) {
        Long userId = jwtProvider.extractUserIdFromRefreshToken(refreshToken);

        String redisKey = "RT:" + userId;

        // getAndDelete: 조회와 삭제를 원자적으로 수행.
        // 동시 요청이 두 번 조회해 각각 유효 판정을 받는 TOCTOU 경합을 방지한다.
        String stored = redisTemplate.opsForValue().getAndDelete(redisKey);

        if (stored == null || !stored.equals(refreshToken)) {
            throw new OnSeoulApiException(ErrorCode.INVALID_REFRESH_TOKEN, "유효하지 않은 리프레시 토큰입니다.");
        }

        // SUSPENDED/DELETED 계정은 Refresh 시점에도 차단
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new OnSeoulApiException(ErrorCode.FORBIDDEN, "사용자를 찾을 수 없습니다."));
        if (user.getStatus() != UserStatus.ACTIVE) {
            throw new OnSeoulApiException(ErrorCode.FORBIDDEN, "비활성화된 계정입니다.");
        }

        String newAccessToken  = jwtProvider.generateAccessToken(userId);
        String newRefreshToken = jwtProvider.generateRefreshToken(userId);

        // Redis TTL = JWT refreshTokenMinutes → 두 값이 항상 동기화됨
        redisTemplate.opsForValue().set(
                redisKey, newRefreshToken,
                jwtProvider.getRefreshTokenMinutes(), TimeUnit.MINUTES);

        return new TokenResponse(newAccessToken, newRefreshToken);
    }

    /**
     * 해당 사용자의 Refresh Token을 Redis에서 삭제한다(로그아웃).
     *
     * @param userId 로그아웃할 사용자 ID
     */
    public void logout(Long userId) {
        redisTemplate.delete("RT:" + userId);
    }
}
