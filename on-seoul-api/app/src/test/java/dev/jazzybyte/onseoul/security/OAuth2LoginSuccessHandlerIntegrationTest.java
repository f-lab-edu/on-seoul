package dev.jazzybyte.onseoul.security;

import dev.jazzybyte.onseoul.domain.User;
import dev.jazzybyte.onseoul.domain.UserStatus;
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
import org.springframework.http.HttpHeaders;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.client.authentication.OAuth2AuthenticationToken;
import org.springframework.security.oauth2.core.user.DefaultOAuth2User;
import org.springframework.security.oauth2.core.user.OAuth2User;
import org.springframework.test.util.ReflectionTestUtils;

import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * OAuth2LoginSuccessHandler 통합 검증.
 * Spring 컨텍스트 없이 핸들러 로직(upsert, JWT 발급, Redis 저장, 쿠키 설정, 리다이렉트)을 직접 검증한다.
 */
@ExtendWith(MockitoExtension.class)
class OAuth2LoginSuccessHandlerIntegrationTest {

    private static final String TEST_SECRET =
            "dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=";
    private static final String FRONTEND_BASE_URL = "http://localhost:3000";

    @Mock private UserRepository userRepository;
    @Mock private StringRedisTemplate redisTemplate;
    @Mock private ValueOperations<String, String> valueOps;

    private OAuth2LoginSuccessHandler handler;
    private JwtProvider jwtProvider;

    @BeforeEach
    void setUp() {
        jwtProvider = new JwtProvider(TEST_SECRET, 15L, 10080L);
        // cookieSecure=false: 테스트 환경은 HTTP
        handler = new OAuth2LoginSuccessHandler(
                userRepository, jwtProvider, redisTemplate, FRONTEND_BASE_URL, false);
    }

    private void stubRedis() {
        when(redisTemplate.opsForValue()).thenReturn(valueOps);
    }

    // ── 헬퍼 ──────────────────────────────────────────────────────

    private User stubUser(long id, String provider, String providerId) {
        User user = User.builder()
                .provider(provider).providerId(providerId)
                .email("test@example.com").nickname("테스터")
                .build();
        ReflectionTestUtils.setField(user, "id", id);
        return user;
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

    /** Set-Cookie 헤더 목록에서 특정 쿠키 이름을 가진 값을 찾아 반환한다. */
    private String findSetCookieValue(MockHttpServletResponse res, String cookieName) {
        Collection<String> headers = res.getHeaders(HttpHeaders.SET_COOKIE);
        return headers.stream()
                .filter(h -> h.startsWith(cookieName + "="))
                .findFirst()
                .orElse(null);
    }

    // ── Google ────────────────────────────────────────────────────

    @Test
    @DisplayName("Google 신규 로그인 — users INSERT + 쿠키 설정 + 성공 리다이렉트")
    void google_new_user_sets_cookies_and_redirects() throws Exception {
        Map<String, Object> attrs = Map.of(
                "id", "google-uid-001",
                "email", "new@gmail.com",
                "name", "홍길동");
        User saved = stubUser(1L, "google", "google-uid-001");

        when(userRepository.findByProviderAndProviderId("google", "google-uid-001"))
                .thenReturn(Optional.empty());
        when(userRepository.save(any())).thenReturn(saved);
        stubRedis();

        MockHttpServletResponse res = invoke(googleToken(attrs));

        // 302 리다이렉트 — 성공 콜백 URL
        assertThat(res.getStatus()).isEqualTo(302);
        assertThat(res.getRedirectedUrl()).isEqualTo(FRONTEND_BASE_URL + "/oauth/callback?status=success");

        // access_token 쿠키: HttpOnly, maxAge=900, path="/"
        String accessHeader = findSetCookieValue(res, OAuth2LoginSuccessHandler.ACCESS_TOKEN_COOKIE);
        assertThat(accessHeader).isNotNull()
                .contains("HttpOnly")
                .contains("Max-Age=900")
                .contains("Path=/");

        // refresh_token 쿠키: HttpOnly, maxAge=604800, path="/auth"
        String refreshHeader = findSetCookieValue(res, OAuth2LoginSuccessHandler.REFRESH_TOKEN_COOKIE);
        assertThat(refreshHeader).isNotNull()
                .contains("HttpOnly")
                .contains("Max-Age=604800")
                .contains("Path=/auth");

        // Redis에 RT 저장
        // TTL = jwt.refresh-token-minutes (10080 = 7일). JwtProvider 단일 소스
        verify(valueOps).set(eq("RT:1"), anyString(), eq(10080L), eq(TimeUnit.MINUTES));
    }

    @Test
    @DisplayName("Google 재로그인 — 기존 user UPDATE (email/nickname 갱신, INSERT 아님)")
    void google_existing_user_updates_profile_not_insert() throws Exception {
        Map<String, Object> attrs = Map.of(
                "id", "google-uid-001",
                "email", "updated@gmail.com",
                "name", "갱신이름");
        User existing = stubUser(1L, "google", "google-uid-001");

        when(userRepository.findByProviderAndProviderId("google", "google-uid-001"))
                .thenReturn(Optional.of(existing));
        when(userRepository.save(any())).thenReturn(existing);
        stubRedis();

        MockHttpServletResponse res = invoke(googleToken(attrs));

        assertThat(res.getStatus()).isEqualTo(302);
        verify(userRepository).save(existing);
        verify(userRepository, never()).save(argThat(u -> u != existing));
    }

    @Test
    @DisplayName("SUSPENDED 계정 — 에러 리다이렉트, 쿠키 미발급, Redis 미저장")
    void suspended_user_redirects_to_error_without_tokens() throws Exception {
        Map<String, Object> attrs = Map.of(
                "id", "google-uid-002",
                "email", "bad@gmail.com",
                "name", "정지된사용자");
        User suspended = stubUser(2L, "google", "google-uid-002");
        ReflectionTestUtils.setField(suspended, "status", UserStatus.SUSPENDED);

        when(userRepository.findByProviderAndProviderId(any(), any()))
                .thenReturn(Optional.of(suspended));
        when(userRepository.save(any())).thenReturn(suspended);

        MockHttpServletResponse res = invoke(googleToken(attrs));

        // 에러 리다이렉트
        assertThat(res.getStatus()).isEqualTo(302);
        assertThat(res.getRedirectedUrl()).isEqualTo(FRONTEND_BASE_URL + "/oauth/callback?error=forbidden");

        // 토큰 쿠키 미발급
        assertThat(findSetCookieValue(res, OAuth2LoginSuccessHandler.ACCESS_TOKEN_COOKIE)).isNull();
        assertThat(findSetCookieValue(res, OAuth2LoginSuccessHandler.REFRESH_TOKEN_COOKIE)).isNull();
        verifyNoInteractions(valueOps);
    }

    // ── Kakao ─────────────────────────────────────────────────────

    @Test
    @DisplayName("Kakao 신규 로그인 — 중첩 속성(kakao_account/properties) 파싱 + 쿠키 설정")
    void kakao_new_user_parses_nested_attributes_and_sets_cookies() throws Exception {
        Map<String, Object> kakaoAccount = new HashMap<>();
        kakaoAccount.put("email", "kakao@kakao.com");
        Map<String, Object> properties = new HashMap<>();
        properties.put("nickname", "카카오유저");

        Map<String, Object> attrs = new HashMap<>();
        attrs.put("id", 9876543L);
        attrs.put("kakao_account", kakaoAccount);
        attrs.put("properties", properties);

        User saved = stubUser(3L, "kakao", "9876543");
        when(userRepository.findByProviderAndProviderId("kakao", "9876543"))
                .thenReturn(Optional.empty());
        when(userRepository.save(any())).thenReturn(saved);
        stubRedis();

        MockHttpServletResponse res = invoke(kakaoToken(attrs));

        assertThat(res.getStatus()).isEqualTo(302);
        assertThat(res.getRedirectedUrl()).contains("status=success");
        assertThat(findSetCookieValue(res, OAuth2LoginSuccessHandler.ACCESS_TOKEN_COOKIE)).isNotNull();
        verify(valueOps).set(eq("RT:3"), anyString(), eq(10080L), eq(TimeUnit.MINUTES));
    }

    @Test
    @DisplayName("Kakao email 미동의(kakao_account null) — email null이어도 정상 가입")
    void kakao_without_email_scope_still_registers() throws Exception {
        Map<String, Object> attrs = new HashMap<>();
        attrs.put("id", 1111111L);
        attrs.put("kakao_account", null);
        attrs.put("properties", Map.of("nickname", "익명카카오"));

        User saved = stubUser(4L, "kakao", "1111111");
        when(userRepository.findByProviderAndProviderId("kakao", "1111111"))
                .thenReturn(Optional.empty());
        when(userRepository.save(any())).thenReturn(saved);
        stubRedis();

        MockHttpServletResponse res = invoke(kakaoToken(attrs));

        assertThat(res.getStatus()).isEqualTo(302);
    }
}
