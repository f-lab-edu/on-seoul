package dev.jazzybyte.onseoul.user.adapter.in.security;

import dev.jazzybyte.onseoul.user.port.in.SocialLoginCommand;
import dev.jazzybyte.onseoul.user.port.in.SocialLoginUseCase;
import dev.jazzybyte.onseoul.user.port.in.TokenResponse;
import dev.jazzybyte.onseoul.user.port.out.TokenIssuerPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseCookie;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.client.authentication.OAuth2AuthenticationToken;
import org.springframework.security.oauth2.core.user.OAuth2User;
import org.springframework.security.web.authentication.AuthenticationSuccessHandler;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.time.Duration;
import java.util.Map;

/**
 * OAuth2 로그인 성공 시 Access/Refresh 토큰을 HttpOnly 쿠키로 발급하고
 * 프론트엔드 콜백 URL로 리다이렉트한다.
 *
 * <p>성공: {@code {frontendBaseUrl}/oauth/callback?status=success}</p>
 * <p>SUSPENDED/DELETED 계정: {@code {frontendBaseUrl}/oauth/callback?error=forbidden}</p>
 *
 * <p>이 핸들러의 책임은 소셜 로그인 처리 후 토큰 발급과 콜백 리다이렉트뿐이다.
 * 알림 구독은 opt-in 모델이므로 신규 사용자에게 기본 구독을 생성하지 않는다.</p>
 */
@Slf4j
@Component
public class OAuth2LoginSuccessHandler implements AuthenticationSuccessHandler {

    /** HttpOnly 쿠키로 전달되는 Access Token 쿠키 이름. JwtAuthenticationFilter와 공유. */
    public static final String ACCESS_TOKEN_COOKIE  = "access_token";
    /** HttpOnly 쿠키로 전달되는 Refresh Token 쿠키 이름. AuthController와 공유. */
    public static final String REFRESH_TOKEN_COOKIE = "refresh_token";

    private final SocialLoginUseCase socialLoginUseCase;
    private final String frontendBaseUrl;
    private final boolean cookieSecure;
    /** 쿠키 SameSite 속성. 기본값 Strict. */
    private final String cookieSameSite;
    /**
     * 쿠키 Domain 속성. 서브도메인 간 공유 시 ".jazzz.dev" 형식으로 설정.
     * 빈 값이면 응답 호스트에만 스코프됨(로컬 개발 기본값).
     */
    private final String cookieDomain;
    /** Access Token 쿠키 maxAge. JWT TTL과 동기화 — TokenIssuerPort 단일 소스. */
    private final long accessTokenMinutes;
    /** Refresh Token 쿠키 maxAge. JWT TTL과 동기화 — TokenIssuerPort 단일 소스. */
    private final long refreshTokenMinutes;

    public OAuth2LoginSuccessHandler(
            final SocialLoginUseCase socialLoginUseCase,
            final TokenIssuerPort tokenIssuerPort,
            @Value("${app.frontend-base-url}") String frontendBaseUrl,
            @Value("${app.cookie-secure:true}") boolean cookieSecure,
            @Value("${app.cookie-same-site:Strict}") String cookieSameSite,
            @Value("${app.cookie-domain:}") String cookieDomain) {
        this.socialLoginUseCase = socialLoginUseCase;
        this.frontendBaseUrl = frontendBaseUrl;
        this.cookieSecure = cookieSecure;
        this.cookieSameSite = cookieSameSite;
        this.cookieDomain = cookieDomain;
        this.accessTokenMinutes  = tokenIssuerPort.getAccessTokenMinutes();
        this.refreshTokenMinutes = tokenIssuerPort.getRefreshTokenMinutes();
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

        if (providerId == null) {
            log.warn("[Security] OAuth2 로그인 실패: providerId 누락 - (provider={})", provider);
            response.sendRedirect(frontendBaseUrl + "/oauth/callback?error=server_error");
            return;
        }

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

        try {
            SocialLoginCommand command = new SocialLoginCommand(provider, providerId, email, nickname);
            TokenResponse tokenResponse = socialLoginUseCase.socialLogin(command);

            response.addHeader("Set-Cookie", buildAccessCookie(tokenResponse.accessToken()).toString());
            response.addHeader("Set-Cookie", buildRefreshCookie(tokenResponse.refreshToken()).toString());
            log.info("[Security] OAuth2 로그인 성공: provider={}, providerId={}", provider, providerId);
            response.sendRedirect(frontendBaseUrl + "/oauth/callback?status=success");

        } catch (OnSeoulApiException ex) {
            // FORBIDDEN = SUSPENDED/DELETED 계정. 그 외 OnSeoulApiException은 서버 내부 오류.
            String errorParam = ex.getErrorCode() == ErrorCode.FORBIDDEN ? "forbidden" : "server_error";
            log.warn("[Security] OAuth2 로그인 실패: provider={}, providerId={}, error={}",
                    provider, providerId, ex.getMessage());
            response.sendRedirect(frontendBaseUrl + "/oauth/callback?error=" + errorParam);
        } catch (org.springframework.dao.DataAccessException ex) {
            // Redis timeout(QueryTimeoutException) 등 데이터 접근 오류.
            // catch하지 않으면 500으로 죽어 프론트 리다이렉트가 수행되지 않는다.
            log.error("[Security] OAuth2 로그인 중 Redis 오류: provider={}, error={}", provider, ex.getMessage());
            response.sendRedirect(frontendBaseUrl + "/oauth/callback?error=server_error");
        }
    }

    /**
     * Access Token HttpOnly 쿠키를 생성한다.
     * maxAge = accessTokenMinutes(분), path = "/".
     */
    public ResponseCookie buildAccessCookie(String token) {
        return applyDomain(ResponseCookie.from(ACCESS_TOKEN_COOKIE, token)
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite(cookieSameSite)
                .path("/")
                .maxAge(Duration.ofMinutes(accessTokenMinutes)))
                .build();
    }

    /**
     * Refresh Token HttpOnly 쿠키를 생성한다.
     * maxAge = refreshTokenMinutes(분), path = "/auth".
     */
    public ResponseCookie buildRefreshCookie(String token) {
        return applyDomain(ResponseCookie.from(REFRESH_TOKEN_COOKIE, token)
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite(cookieSameSite)
                .path("/auth")
                .maxAge(Duration.ofMinutes(refreshTokenMinutes)))
                .build();
    }

    /**
     * Access Token 쿠키를 만료시킨다 (로그아웃용).
     * maxAge = 0, path = "/".
     */
    public ResponseCookie expireAccessCookie() {
        return applyDomain(ResponseCookie.from(ACCESS_TOKEN_COOKIE, "")
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite(cookieSameSite)
                .path("/")
                .maxAge(0))
                .build();
    }

    /**
     * Refresh Token 쿠키를 만료시킨다 (로그아웃용).
     * maxAge = 0, path = "/auth".
     */
    public ResponseCookie expireRefreshCookie() {
        return applyDomain(ResponseCookie.from(REFRESH_TOKEN_COOKIE, "")
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite(cookieSameSite)
                .path("/auth")
                .maxAge(0))
                .build();
    }

    /** cookieDomain이 설정된 경우에만 domain 속성을 적용한다. */
    private ResponseCookie.ResponseCookieBuilder applyDomain(ResponseCookie.ResponseCookieBuilder builder) {
        if (cookieDomain != null && !cookieDomain.isBlank()) {
            builder.domain(cookieDomain);
        }
        return builder;
    }
}
