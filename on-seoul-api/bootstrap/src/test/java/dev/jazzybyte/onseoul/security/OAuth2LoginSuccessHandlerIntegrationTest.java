package dev.jazzybyte.onseoul.security;

import dev.jazzybyte.onseoul.adapter.in.security.JwtTokenIssuer;
import dev.jazzybyte.onseoul.adapter.in.security.OAuth2LoginSuccessHandler;
import dev.jazzybyte.onseoul.domain.port.in.SocialLoginCommand;
import dev.jazzybyte.onseoul.domain.port.in.SocialLoginUseCase;
import dev.jazzybyte.onseoul.domain.port.in.TokenResponse;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.client.authentication.OAuth2AuthenticationToken;
import org.springframework.security.oauth2.core.user.DefaultOAuth2User;
import org.springframework.security.oauth2.core.user.OAuth2User;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

/**
 * OAuth2LoginSuccessHandler 통합 검증.
 * SocialLoginUseCase를 mock으로 대체해 핸들러 로직을 직접 검증한다.
 */
@ExtendWith(MockitoExtension.class)
class OAuth2LoginSuccessHandlerIntegrationTest {

    private static final String TEST_SECRET =
            "dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=";

    private static final String FRONTEND_BASE_URL = "http://localhost:3000";

    @Mock private SocialLoginUseCase socialLoginUseCase;

    private OAuth2LoginSuccessHandler handler;
    private JwtTokenIssuer tokenIssuer;

    @BeforeEach
    void setUp() {
        tokenIssuer = new JwtTokenIssuer(TEST_SECRET, 15L, 10080L);
        handler = new OAuth2LoginSuccessHandler(socialLoginUseCase, tokenIssuer, FRONTEND_BASE_URL, false);
    }

    private OAuth2AuthenticationToken googleToken(Map<String, Object> attrs) {
        OAuth2User oauth2User = new DefaultOAuth2User(
                List.of(new SimpleGrantedAuthority("ROLE_USER")), attrs, "id");
        return new OAuth2AuthenticationToken(oauth2User, oauth2User.getAuthorities(), "google");
    }

    private OAuth2AuthenticationToken kakaoToken(Map<String, Object> attrs) {
        OAuth2User oauth2User = new DefaultOAuth2User(
                List.of(new SimpleGrantedAuthority("ROLE_USER")), attrs, "id");
        return new OAuth2AuthenticationToken(oauth2User, oauth2User.getAuthorities(), "kakao");
    }

    private MockHttpServletResponse invoke(OAuth2AuthenticationToken token) throws Exception {
        MockHttpServletRequest req = new MockHttpServletRequest();
        MockHttpServletResponse res = new MockHttpServletResponse();
        handler.onAuthenticationSuccess(req, res, token);
        return res;
    }

    @Test
    @DisplayName("Google 신규 로그인 — SocialLoginUseCase 호출 후 JWT 반환")
    void google_new_user_returns_jwt() throws Exception {
        Map<String, Object> attrs = Map.of(
                "id", "google-uid-001",
                "email", "new@gmail.com",
                "name", "홍길동");

        String accessToken = tokenIssuer.generateAccessToken(1L);
        String refreshToken = tokenIssuer.generateRefreshToken(1L);
        when(socialLoginUseCase.socialLogin(any(SocialLoginCommand.class)))
                .thenReturn(new TokenResponse(accessToken, refreshToken));

        MockHttpServletResponse res = invoke(googleToken(attrs));

        assertThat(res.getStatus()).isEqualTo(302);
        assertThat(res.getRedirectedUrl()).isEqualTo(FRONTEND_BASE_URL + "/oauth/callback?status=success");
        assertThat(res.getHeader("Set-Cookie")).contains("access_token=");
    }

    @Test
    @DisplayName("SUSPENDED 계정 — SocialLoginUseCase가 FORBIDDEN 예외 → 403 반환")
    void suspended_user_is_blocked() throws Exception {
        Map<String, Object> attrs = Map.of(
                "id", "google-uid-002",
                "email", "bad@gmail.com",
                "name", "정지된사용자");

        when(socialLoginUseCase.socialLogin(any()))
                .thenThrow(new OnSeoulApiException(ErrorCode.FORBIDDEN, "비활성화된 계정입니다."));

        MockHttpServletResponse res = invoke(googleToken(attrs));

        assertThat(res.getStatus()).isEqualTo(302);
        assertThat(res.getRedirectedUrl()).isEqualTo(FRONTEND_BASE_URL + "/oauth/callback?error=forbidden");
    }

    @Test
    @DisplayName("Kakao 로그인 — 중첩 속성 파싱 + SocialLoginUseCase 호출")
    void kakao_login_parses_nested_attributes() throws Exception {
        Map<String, Object> kakaoAccount = new HashMap<>();
        kakaoAccount.put("email", "kakao@kakao.com");
        Map<String, Object> properties = new HashMap<>();
        properties.put("nickname", "카카오유저");

        Map<String, Object> attrs = new HashMap<>();
        attrs.put("id", 9876543L);
        attrs.put("kakao_account", kakaoAccount);
        attrs.put("properties", properties);

        String accessToken = tokenIssuer.generateAccessToken(3L);
        String refreshToken = tokenIssuer.generateRefreshToken(3L);
        when(socialLoginUseCase.socialLogin(any()))
                .thenReturn(new TokenResponse(accessToken, refreshToken));

        MockHttpServletResponse res = invoke(kakaoToken(attrs));

        assertThat(res.getStatus()).isEqualTo(302);
        assertThat(res.getRedirectedUrl()).isEqualTo(FRONTEND_BASE_URL + "/oauth/callback?status=success");
        verify(socialLoginUseCase).socialLogin(argThat(cmd ->
                "kakao".equals(cmd.provider()) && "9876543".equals(cmd.providerId())));
    }

    @Test
    @DisplayName("Kakao email 미동의 — email null이어도 SocialLoginUseCase 호출")
    void kakao_without_email_scope_still_calls_use_case() throws Exception {
        Map<String, Object> attrs = new HashMap<>();
        attrs.put("id", 1111111L);
        attrs.put("kakao_account", null);
        attrs.put("properties", Map.of("nickname", "익명카카오"));

        String accessToken = tokenIssuer.generateAccessToken(4L);
        String refreshToken = tokenIssuer.generateRefreshToken(4L);
        when(socialLoginUseCase.socialLogin(any()))
                .thenReturn(new TokenResponse(accessToken, refreshToken));

        MockHttpServletResponse res = invoke(kakaoToken(attrs));

        assertThat(res.getStatus()).isEqualTo(302);
        assertThat(res.getRedirectedUrl()).isEqualTo(FRONTEND_BASE_URL + "/oauth/callback?status=success");
    }

    // ── 쿠키 속성 검증 ─────────────────────────────────────────────

    @Test
    @DisplayName("성공 시 access_token 쿠키는 HttpOnly; Path=/; maxAge=900 속성을 갖는다")
    void success_access_cookie_has_correct_attributes() throws Exception {
        Map<String, Object> attrs = Map.of("id", "g-001", "email", "a@b.com", "name", "테스트");
        String accessToken = tokenIssuer.generateAccessToken(5L);
        String refreshToken = tokenIssuer.generateRefreshToken(5L);
        when(socialLoginUseCase.socialLogin(any()))
                .thenReturn(new TokenResponse(accessToken, refreshToken));

        MockHttpServletResponse res = invoke(googleToken(attrs));

        List<String> setCookieHeaders = res.getHeaders("Set-Cookie");
        String accessCookie = setCookieHeaders.stream()
                .filter(h -> h.startsWith("access_token="))
                .findFirst()
                .orElseThrow(() -> new AssertionError("access_token 쿠키 없음"));

        assertThat(accessCookie).contains("HttpOnly");
        assertThat(accessCookie).containsIgnoringCase("Path=/");
        assertThat(accessCookie).containsIgnoringCase("Max-Age=900");
        assertThat(accessCookie).containsIgnoringCase("SameSite=Strict");
    }

    @Test
    @DisplayName("성공 시 refresh_token 쿠키는 HttpOnly; Path=/auth 속성을 갖는다")
    void success_refresh_cookie_has_correct_attributes() throws Exception {
        Map<String, Object> attrs = Map.of("id", "g-002", "email", "b@b.com", "name", "테스트2");
        String accessToken = tokenIssuer.generateAccessToken(6L);
        String refreshToken = tokenIssuer.generateRefreshToken(6L);
        when(socialLoginUseCase.socialLogin(any()))
                .thenReturn(new TokenResponse(accessToken, refreshToken));

        MockHttpServletResponse res = invoke(googleToken(attrs));

        List<String> setCookieHeaders = res.getHeaders("Set-Cookie");
        String refreshCookie = setCookieHeaders.stream()
                .filter(h -> h.startsWith("refresh_token="))
                .findFirst()
                .orElseThrow(() -> new AssertionError("refresh_token 쿠키 없음"));

        assertThat(refreshCookie).contains("HttpOnly");
        assertThat(refreshCookie).containsIgnoringCase("Path=/auth");
        assertThat(refreshCookie).containsIgnoringCase("SameSite=Strict");
        // maxAge는 refreshTokenMinutes(10080분)에서 초로 환산
        assertThat(refreshCookie).containsIgnoringCase("Max-Age=604800");
    }

    @Test
    @DisplayName("SUSPENDED 계정 — 에러 리다이렉트 시 쿠키를 발급하지 않는다")
    void suspended_user_does_not_set_cookies() throws Exception {
        Map<String, Object> attrs = Map.of("id", "g-003", "email", "bad@b.com", "name", "정지됨");
        when(socialLoginUseCase.socialLogin(any()))
                .thenThrow(new OnSeoulApiException(ErrorCode.FORBIDDEN, "비활성화된 계정입니다."));

        MockHttpServletResponse res = invoke(googleToken(attrs));

        assertThat(res.getStatus()).isEqualTo(302);
        assertThat(res.getRedirectedUrl()).isEqualTo(FRONTEND_BASE_URL + "/oauth/callback?error=forbidden");
        // 에러 시 쿠키가 발급되어선 안 된다
        assertThat(res.getHeaders("Set-Cookie")).isEmpty();
    }

    @Test
    @DisplayName("buildAccessCookie — cookieSecure=false 일 때 Secure 속성 없음")
    void build_access_cookie_insecure_mode() {
        String cookie = handler.buildAccessCookie("tok").toString();
        assertThat(cookie).doesNotContainIgnoringCase("Secure");
    }

    @Test
    @DisplayName("expireAccessCookie — maxAge=0, 빈 값으로 생성된다")
    void expire_access_cookie_has_max_age_zero() {
        String cookie = handler.expireAccessCookie().toString();
        assertThat(cookie).contains("access_token=");
        assertThat(cookie).containsIgnoringCase("Max-Age=0");
        assertThat(cookie).containsIgnoringCase("Path=/");
    }

    @Test
    @DisplayName("expireRefreshCookie — maxAge=0, Path=/auth 로 생성된다")
    void expire_refresh_cookie_has_max_age_zero_and_auth_path() {
        String cookie = handler.expireRefreshCookie().toString();
        assertThat(cookie).contains("refresh_token=");
        assertThat(cookie).containsIgnoringCase("Max-Age=0");
        assertThat(cookie).containsIgnoringCase("Path=/auth");
    }
}
