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
 * <h3>왜 JSON body 응답이 아닌 쿠키 + 리다이렉트인가</h3>
 * <p>Authorization Code Flow(RFC 6749 §4.1)에서 콜백 URL은 브라우저의 full-page navigation으로
 * 도달한다. 이 시점에서 JSON body를 응답하면 브라우저가 raw JSON을 그대로 렌더링하며,
 * SPA(JavaScript)가 XHR/fetch 없이는 해당 응답을 수신할 방법이 없다.</p>
 *
 * <h3>토큰 전달 방식</h3>
 * <ul>
 *   <li><b>access_token</b> — HttpOnly 쿠키, path="/", maxAge=15분
 *       (JwtAuthenticationFilter가 쿠키 또는 Authorization 헤더로 읽음)</li>
 *   <li><b>refresh_token</b> — HttpOnly 쿠키, path="/auth", maxAge=7일
 *       (path 제한으로 노출 범위를 /auth 하위로 최소화)</li>
 * </ul>
 *
 * <h3>보안 속성</h3>
 * <ul>
 *   <li>{@code HttpOnly} — XSS로부터 토큰 탈취 방지</li>
 *   <li>{@code Secure} — HTTPS에서만 전송 (로컬 개발: {@code COOKIE_SECURE=false})</li>
 *   <li>{@code SameSite=Strict} — CSRF 방어</li>
 * </ul>
 */
@Component
public class OAuth2LoginSuccessHandler implements AuthenticationSuccessHandler {

    /** HttpOnly 쿠키로 전달되는 Access Token 쿠키 이름. JwtAuthenticationFilter와 공유. */
    public static final String ACCESS_TOKEN_COOKIE  = "access_token";
    /** HttpOnly 쿠키로 전달되는 Refresh Token 쿠키 이름. AuthController와 공유. */
    public static final String REFRESH_TOKEN_COOKIE = "refresh_token";

    /** Redis에 저장되는 Refresh Token TTL (7일). application.yml의 refresh-token-minutes와 맞춤. */
    private static final long REFRESH_TOKEN_TTL_DAYS       = 7L;
    /** Access Token 쿠키 maxAge = JWT TTL(15분)과 동일하게 설정. */
    private static final int  ACCESS_TOKEN_MAX_AGE_SECONDS  = 15 * 60;
    /** Refresh Token 쿠키 maxAge = Redis TTL(7일)과 동일하게 설정. */
    private static final long REFRESH_TOKEN_MAX_AGE_SECONDS = 7L * 24 * 60 * 60;

    private final UserRepository userRepository;
    private final JwtProvider jwtProvider;
    private final org.springframework.data.redis.core.StringRedisTemplate redisTemplate;
    /** 리다이렉트 대상 프론트엔드 URL. 환경변수 FRONTEND_BASE_URL로 주입. */
    private final String frontendBaseUrl;
    /** 쿠키 Secure 속성 제어. 프로덕션=true, 로컬 HTTP 개발=false (COOKIE_SECURE=false). */
    private final boolean cookieSecure;

    public OAuth2LoginSuccessHandler(
            final UserRepository userRepository,
            final JwtProvider jwtProvider,
            final org.springframework.data.redis.core.StringRedisTemplate redisTemplate,
            @Value("${app.frontend-base-url}") String frontendBaseUrl,
            @Value("${app.cookie-secure:true}") boolean cookieSecure) {
        this.userRepository  = userRepository;
        this.jwtProvider     = jwtProvider;
        this.redisTemplate   = redisTemplate;
        this.frontendBaseUrl = frontendBaseUrl;
        this.cookieSecure    = cookieSecure;
    }

    /**
     * OAuth2 인증 성공 시 호출된다.
     *
     * <ol>
     *   <li>provider + providerId로 기존 사용자 조회 → 없으면 INSERT, 있으면 email/nickname UPDATE</li>
     *   <li>SUSPENDED/DELETED 계정은 토큰 미발급 후 에러 URL로 리다이렉트</li>
     *   <li>Access/Refresh Token 발급 → Refresh Token을 Redis에 저장 (TTL 7일)</li>
     *   <li>두 토큰을 HttpOnly 쿠키로 설정 후 프론트엔드 콜백 URL로 리다이렉트</li>
     * </ol>
     */
    @Override
    public void onAuthenticationSuccess(HttpServletRequest request,
                                        HttpServletResponse response,
                                        Authentication authentication) throws IOException {
        OAuth2AuthenticationToken oauthToken = (OAuth2AuthenticationToken) authentication;
        OAuth2User oauth2User = oauthToken.getPrincipal();
        String provider = oauthToken.getAuthorizedClientRegistrationId();

        // Google: "sub" 클레임, Kakao: "id" 속성으로 providerId 추출
        Object idAttr = oauth2User.getAttribute("sub") != null
                ? oauth2User.getAttribute("sub")
                : oauth2User.getAttribute("id");
        String providerId = idAttr != null ? idAttr.toString() : null;

        // Kakao는 사용자 정보가 중첩 구조(kakao_account, properties)에 있어 별도 파싱
        String email;
        String nickname;
        if ("kakao".equals(provider)) {
            Map<String, Object> kakaoAccount = oauth2User.getAttribute("kakao_account");
            Map<String, Object> properties   = oauth2User.getAttribute("properties");

            email    = kakaoAccount != null ? (String) kakaoAccount.get("email") : null;
            nickname = properties   != null ? (String) properties.get("nickname") : null;
        } else {
            // Google: flat 구조
            email    = oauth2User.getAttribute("email");
            nickname = oauth2User.getAttribute("name");
        }

        // 기존 사용자면 프로필 갱신(Upsert), 신규면 INSERT
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

        // SUSPENDED/DELETED 계정은 토큰 발급 없이 에러 리다이렉트
        if (user.getStatus() != UserStatus.ACTIVE) {
            response.sendRedirect(frontendBaseUrl + "/oauth/callback?error=forbidden");
            return;
        }

        String accessToken  = jwtProvider.generateAccessToken(user.getId());
        String refreshToken = jwtProvider.generateRefreshToken(user.getId());

        // Refresh Token을 Redis에 저장. 키: "RT:{userId}", TTL = 7일
        // 로그아웃/강제만료 시 이 키를 삭제하면 Refresh Token 무효화 가능
        String redisKey = "RT:" + user.getId();
        redisTemplate.opsForValue().set(redisKey, refreshToken, REFRESH_TOKEN_TTL_DAYS, TimeUnit.DAYS);

        // 두 토큰을 HttpOnly 쿠키로 설정 후 프론트엔드 콜백 페이지로 리다이렉트
        response.addHeader(HttpHeaders.SET_COOKIE, buildAccessCookie(accessToken).toString());
        response.addHeader(HttpHeaders.SET_COOKIE, buildRefreshCookie(refreshToken).toString());
        response.sendRedirect(frontendBaseUrl + "/oauth/callback?status=success");
    }

    /**
     * Access Token 쿠키를 생성한다.
     *
     * <p>path="/"로 설정하여 모든 API 요청에 자동 전송되게 한다.
     * JwtAuthenticationFilter가 Authorization 헤더 없을 때 이 쿠키를 폴백으로 읽는다.</p>
     *
     * @param token 발급된 Access Token JWT 문자열
     */
    public ResponseCookie buildAccessCookie(String token) {
        return ResponseCookie.from(ACCESS_TOKEN_COOKIE, token)
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite("Strict")
                .path("/")
                .maxAge(ACCESS_TOKEN_MAX_AGE_SECONDS)
                .build();
    }

    /**
     * Refresh Token 쿠키를 생성한다.
     *
     * <p>path="/auth"로 제한하여 refresh/logout 요청에만 전송되도록 노출 범위를 줄인다.</p>
     *
     * @param token 발급된 Refresh Token JWT 문자열
     */
    public ResponseCookie buildRefreshCookie(String token) {
        return ResponseCookie.from(REFRESH_TOKEN_COOKIE, token)
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite("Strict")
                .path("/auth")
                .maxAge(REFRESH_TOKEN_MAX_AGE_SECONDS)
                .build();
    }

    /**
     * Access Token 쿠키를 만료시킨다 (로그아웃 용).
     *
     * <p>maxAge=0으로 설정하면 브라우저가 즉시 쿠키를 삭제한다.
     * path, Secure, SameSite는 원본 쿠키와 동일하게 맞춰야 정상 삭제된다.</p>
     */
    public ResponseCookie expireAccessCookie() {
        return ResponseCookie.from(ACCESS_TOKEN_COOKIE, "")
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite("Strict")
                .path("/")
                .maxAge(0)
                .build();
    }

    /**
     * Refresh Token 쿠키를 만료시킨다 (로그아웃 용).
     *
     * <p>maxAge=0으로 설정하면 브라우저가 즉시 쿠키를 삭제한다.
     * path, Secure, SameSite는 원본 쿠키와 동일하게 맞춰야 정상 삭제된다.</p>
     */
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
