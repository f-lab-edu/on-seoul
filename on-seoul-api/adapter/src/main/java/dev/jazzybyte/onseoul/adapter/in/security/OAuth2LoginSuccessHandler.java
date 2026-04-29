package dev.jazzybyte.onseoul.adapter.in.security;

import dev.jazzybyte.onseoul.domain.port.in.SocialLoginCommand;
import dev.jazzybyte.onseoul.domain.port.in.SocialLoginUseCase;
import dev.jazzybyte.onseoul.domain.port.in.TokenResponse;
import dev.jazzybyte.onseoul.domain.port.out.TokenIssuerPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
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
 */
@Component
public class OAuth2LoginSuccessHandler implements AuthenticationSuccessHandler {

    /** HttpOnly 쿠키로 전달되는 Access Token 쿠키 이름. JwtAuthenticationFilter와 공유. */
    public static final String ACCESS_TOKEN_COOKIE  = "access_token";
    /** HttpOnly 쿠키로 전달되는 Refresh Token 쿠키 이름. AuthController와 공유. */
    public static final String REFRESH_TOKEN_COOKIE = "refresh_token";

    private final SocialLoginUseCase socialLoginUseCase;
    private final String frontendBaseUrl;
    private final boolean cookieSecure;
    /** Access Token 쿠키 maxAge. JWT TTL과 동기화 — TokenIssuerPort 단일 소스. */
    private final long accessTokenMinutes;
    /** Refresh Token 쿠키 maxAge. JWT TTL과 동기화 — TokenIssuerPort 단일 소스. */
    private final long refreshTokenMinutes;

    public OAuth2LoginSuccessHandler(
            final SocialLoginUseCase socialLoginUseCase,
            final TokenIssuerPort tokenIssuerPort,
            @Value("${app.frontend-base-url}") String frontendBaseUrl,
            @Value("${app.cookie-secure:true}") boolean cookieSecure) {
        this.socialLoginUseCase = socialLoginUseCase;
        this.frontendBaseUrl = frontendBaseUrl;
        this.cookieSecure = cookieSecure;
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
            response.sendRedirect(frontendBaseUrl + "/oauth/callback?status=success");

        } catch (OnSeoulApiException ex) {
            // FORBIDDEN = SUSPENDED/DELETED 계정. 그 외 OnSeoulApiException은 서버 내부 오류.
            String errorParam = ex.getErrorCode() == ErrorCode.FORBIDDEN ? "forbidden" : "server_error";
            response.sendRedirect(frontendBaseUrl + "/oauth/callback?error=" + errorParam);
        }
    }

    /**
     * Access Token HttpOnly 쿠키를 생성한다.
     * maxAge = accessTokenMinutes(분), path = "/".
     */
    public ResponseCookie buildAccessCookie(String token) {
        return ResponseCookie.from(ACCESS_TOKEN_COOKIE, token)
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite("Strict")
                .path("/")
                .maxAge(Duration.ofMinutes(accessTokenMinutes))
                .build();
    }

    /**
     * Refresh Token HttpOnly 쿠키를 생성한다.
     * maxAge = refreshTokenMinutes(분), path = "/auth".
     */
    public ResponseCookie buildRefreshCookie(String token) {
        return ResponseCookie.from(REFRESH_TOKEN_COOKIE, token)
                .httpOnly(true)
                .secure(cookieSecure)
                .sameSite("Strict")
                .path("/auth")
                .maxAge(Duration.ofMinutes(refreshTokenMinutes))
                .build();
    }

    /**
     * Access Token 쿠키를 만료시킨다 (로그아웃용).
     * maxAge = 0, path = "/".
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
     * Refresh Token 쿠키를 만료시킨다 (로그아웃용).
     * maxAge = 0, path = "/auth".
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
