package dev.jazzybyte.onseoul.security;

import dev.jazzybyte.onseoul.domain.User;
import dev.jazzybyte.onseoul.domain.UserStatus;
import dev.jazzybyte.onseoul.repository.UserRepository;
import dev.jazzybyte.onseoul.security.jwt.JwtProvider;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseCookie;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.client.authentication.OAuth2AuthenticationToken;
import org.springframework.security.oauth2.core.user.OAuth2User;
import org.springframework.security.web.authentication.AuthenticationSuccessHandler;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * OAuth2 소셜 로그인 성공 핸들러.
 *
 * <p>Authorization Code Flow에서 콜백은 브라우저의 full-page navigation이므로
 * JSON body 응답은 SPA가 수신할 수 없다. 대신 토큰을 HttpOnly 쿠키에 담고
 * 프론트엔드 콜백 URL로 리다이렉트한다.</p>
 *
 * <ul>
 *   <li>{@code access_token} — path="/", maxAge=15분</li>
 *   <li>{@code refresh_token} — path="/auth", maxAge=7일 (노출 범위 최소화)</li>
 * </ul>
 */
@Component
public class OAuth2LoginSuccessHandler implements AuthenticationSuccessHandler {

    public static final String ACCESS_TOKEN_COOKIE  = "access_token";
    public static final String REFRESH_TOKEN_COOKIE = "refresh_token";

    private static final long REFRESH_TOKEN_TTL_DAYS       = 7L;
    private static final int  ACCESS_TOKEN_MAX_AGE_SECONDS  = 15 * 60;
    private static final long REFRESH_TOKEN_MAX_AGE_SECONDS = 7L * 24 * 60 * 60;

    private final UserRepository userRepository;
    private final JwtProvider jwtProvider;
    private final org.springframework.data.redis.core.StringRedisTemplate redisTemplate;
    private final String frontendBaseUrl;
    private final boolean cookieSecure;

    public OAuth2LoginSuccessHandler(
            final UserRepository userRepository,
            final JwtProvider jwtProvider,
            final org.springframework.data.redis.core.StringRedisTemplate redisTemplate,
            @Value("${app.frontend-base-url}") String frontendBaseUrl,
            @Value("${app.cookie-secure:true}") boolean cookieSecure) {
        this.userRepository = userRepository;
        this.jwtProvider    = jwtProvider;
        this.redisTemplate  = redisTemplate;
        this.frontendBaseUrl = frontendBaseUrl;
        this.cookieSecure   = cookieSecure;
    }

    @Override
    public void onAuthenticationSuccess(HttpServletRequest request,
                                        HttpServletResponse response,
                                        Authentication authentication) throws IOException {
        OAuth2AuthenticationToken oauthToken = (OAuth2AuthenticationToken) authentication;
        OAuth2User oauth2User = oauthToken.getPrincipal();
        String provider = oauthToken.getAuthorizedClientRegistrationId();

        Object idAttr = oauth2User.getAttribute("sub") != null
                ? oauth2User.getAttribute("sub")
                : oauth2User.getAttribute("id");
        String providerId = idAttr != null ? idAttr.toString() : null;

        String email;
        String nickname;
        if ("kakao".equals(provider)) {
            Map<String, Object> kakaoAccount = oauth2User.getAttribute("kakao_account");
            Map<String, Object> properties   = oauth2User.getAttribute("properties");
            email    = kakaoAccount != null ? (String) kakaoAccount.get("email") : null;
            nickname = properties   != null ? (String) properties.get("nickname") : null;
        } else {
            email    = oauth2User.getAttribute("email");
            nickname = oauth2User.getAttribute("name");
        }

        User user = userRepository.findByProviderAndProviderId(provider, providerId)
                .map(existing -> {
                    existing.updateProfile(email, nickname);
                    return userRepository.save(existing);
                })
                .orElseGet(() -> userRepository.save(
                        User.builder()
                                .provider(provider)
                                .providerId(providerId)
                                .email(email)
                                .nickname(nickname)
                                .build()
                ));

        // SUSPENDED / DELETED 계정은 에러 페이지로 리다이렉트
        if (user.getStatus() != UserStatus.ACTIVE) {
            response.sendRedirect(frontendBaseUrl + "/oauth/callback?error=forbidden");
            return;
        }

        String accessToken  = jwtProvider.generateAccessToken(user.getId());
        String refreshToken = jwtProvider.generateRefreshToken(user.getId());

        String redisKey = "RT:" + user.getId();
        redisTemplate.opsForValue().set(redisKey, refreshToken, REFRESH_TOKEN_TTL_DAYS, TimeUnit.DAYS);

        response.addHeader(HttpHeaders.SET_COOKIE, buildAccessCookie(accessToken).toString());
        response.addHeader(HttpHeaders.SET_COOKIE, buildRefreshCookie(refreshToken).toString());
        response.sendRedirect(frontendBaseUrl + "/oauth/callback?status=success");
    }

    public ResponseCookie buildAccessCookie(String token) {
        return ResponseCookie.from(ACCESS_TOKEN_COOKIE, token)
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite("Strict")
                .path("/")
                .maxAge(ACCESS_TOKEN_MAX_AGE_SECONDS)
                .build();
    }

    public ResponseCookie buildRefreshCookie(String token) {
        return ResponseCookie.from(REFRESH_TOKEN_COOKIE, token)
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite("Strict")
                .path("/auth")
                .maxAge(REFRESH_TOKEN_MAX_AGE_SECONDS)
                .build();
    }

    public ResponseCookie expireAccessCookie() {
        return ResponseCookie.from(ACCESS_TOKEN_COOKIE, "")
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite("Strict")
                .path("/")
                .maxAge(0)
                .build();
    }

    public ResponseCookie expireRefreshCookie() {
        return ResponseCookie.from(REFRESH_TOKEN_COOKIE, "")
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite("Strict")
                .path("/auth")
                .maxAge(0)
                .build();
    }
}
