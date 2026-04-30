package dev.jazzybyte.onseoul.adapter.in.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.oauth2.core.AuthorizationGrantType;
import org.springframework.security.oauth2.core.endpoint.OAuth2AuthorizationRequest;

import jakarta.servlet.http.Cookie;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

class CookieOAuth2AuthorizationRequestRepositoryTest {

    private CookieOAuth2AuthorizationRequestRepository repository;

    @BeforeEach
    void setUp() {
        repository = new CookieOAuth2AuthorizationRequestRepository(new ObjectMapper(), false);
    }

    private OAuth2AuthorizationRequest buildRequest(String state) {
        return OAuth2AuthorizationRequest.authorizationCode()
                .authorizationUri("https://accounts.google.com/o/oauth2/auth")
                .clientId("test-client-id")
                .redirectUri("http://localhost:8080/login/oauth2/code/google")
                .scopes(Set.of("openid", "email", "profile"))
                .state(state)
                .additionalParameters(Map.of("access_type", "offline"))
                .attributes(Map.of("registration_id", "google"))
                .authorizationRequestUri("https://accounts.google.com/o/oauth2/auth?response_type=code")
                .build();
    }

    // ── save & load ──────────────────────────────────────────────

    @Test
    @DisplayName("saveAuthorizationRequest — Set-Cookie 헤더가 oauth2_auth_request 쿠키를 발급한다")
    void save_setsSetCookieHeader() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();

        repository.saveAuthorizationRequest(buildRequest("state-123"), request, response);

        String setCookie = response.getHeader("Set-Cookie");
        assertThat(setCookie).isNotNull()
                .contains(CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME + "=")
                .contains("HttpOnly")
                .contains("Max-Age=600")
                .contains("SameSite=Lax");
    }

    @Test
    @DisplayName("loadAuthorizationRequest — 저장된 쿠키에서 state를 포함한 요청을 복원한다")
    void saveAndLoad_roundTrip_preservesState() throws Exception {
        MockHttpServletRequest saveReq = new MockHttpServletRequest();
        MockHttpServletResponse saveRes = new MockHttpServletResponse();
        OAuth2AuthorizationRequest original = buildRequest("round-trip-state");

        repository.saveAuthorizationRequest(original, saveReq, saveRes);

        // 저장된 쿠키 값을 꺼내 다음 요청에 심기
        String cookieValue = saveRes.getHeader("Set-Cookie")
                .replaceAll(".*" + CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME + "=([^;]+).*", "$1");

        MockHttpServletRequest loadReq = new MockHttpServletRequest();
        loadReq.setCookies(new Cookie(CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME, cookieValue));

        OAuth2AuthorizationRequest loaded = repository.loadAuthorizationRequest(loadReq);

        assertThat(loaded).isNotNull();
        assertThat(loaded.getState()).isEqualTo("round-trip-state");
        assertThat(loaded.getClientId()).isEqualTo("test-client-id");
        assertThat(loaded.getGrantType()).isEqualTo(AuthorizationGrantType.AUTHORIZATION_CODE);
        assertThat(loaded.getScopes()).containsExactlyInAnyOrder("openid", "email", "profile");
        assertThat(loaded.getAdditionalParameters()).containsEntry("access_type", "offline");
        assertThat(loaded.getAttributes()).containsEntry("registration_id", "google");
    }

    @Test
    @DisplayName("loadAuthorizationRequest — 쿠키 없으면 null 반환")
    void load_noCookie_returnsNull() {
        MockHttpServletRequest request = new MockHttpServletRequest();

        assertThat(repository.loadAuthorizationRequest(request)).isNull();
    }

    @Test
    @DisplayName("loadAuthorizationRequest — 깨진(탬퍼링된) 쿠키면 null 반환")
    void load_tamperedCookie_returnsNull() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setCookies(new Cookie(CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME, "!!!invalid-base64!!!"));

        assertThat(repository.loadAuthorizationRequest(request)).isNull();
    }

    @Test
    @DisplayName("loadAuthorizationRequest — 유효한 Base64지만 JSON이 아닌 경우 null 반환")
    void load_validBase64ButInvalidJson_returnsNull() {
        String notJson = Base64.getUrlEncoder().withoutPadding()
                .encodeToString("not-json".getBytes(StandardCharsets.UTF_8));
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setCookies(new Cookie(CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME, notJson));

        assertThat(repository.loadAuthorizationRequest(request)).isNull();
    }

    // ── remove ───────────────────────────────────────────────────

    @Test
    @DisplayName("removeAuthorizationRequest — 저장된 요청을 반환하고 쿠키를 만료시킨다")
    void remove_returnsSavedRequestAndExpiresCookie() {
        // save
        MockHttpServletRequest saveReq = new MockHttpServletRequest();
        MockHttpServletResponse saveRes = new MockHttpServletResponse();
        repository.saveAuthorizationRequest(buildRequest("remove-state"), saveReq, saveRes);

        String cookieValue = saveRes.getHeader("Set-Cookie")
                .replaceAll(".*" + CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME + "=([^;]+).*", "$1");

        // remove
        MockHttpServletRequest removeReq = new MockHttpServletRequest();
        removeReq.setCookies(new Cookie(CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME, cookieValue));
        MockHttpServletResponse removeRes = new MockHttpServletResponse();

        OAuth2AuthorizationRequest removed = repository.removeAuthorizationRequest(removeReq, removeRes);

        assertThat(removed).isNotNull();
        assertThat(removed.getState()).isEqualTo("remove-state");

        // 쿠키 만료(Max-Age=0) 확인
        String expiredCookie = removeRes.getHeader("Set-Cookie");
        assertThat(expiredCookie).contains("Max-Age=0");
    }

    // ── saveAuthorizationRequest(null) ───────────────────────────

    @Test
    @DisplayName("saveAuthorizationRequest(null) — 기존 쿠키를 만료시킨다")
    void save_null_expiresCookie() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();

        repository.saveAuthorizationRequest(null, request, response);

        assertThat(response.getHeader("Set-Cookie")).contains("Max-Age=0");
    }

    // ── 쿠키 보안 속성 ────────────────────────────────────────────

    @Test
    @DisplayName("cookieSecure=false — Secure 속성 없음 (로컬 개발 환경)")
    void save_insecureMode_noSecureAttribute() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();

        repository.saveAuthorizationRequest(buildRequest("s"), request, response);

        assertThat(response.getHeader("Set-Cookie")).doesNotContainIgnoringCase("Secure");
    }

    @Test
    @DisplayName("cookieSecure=true — Secure 속성 포함 (프로덕션)")
    void save_secureMode_hasSecureAttribute() {
        CookieOAuth2AuthorizationRequestRepository secureRepo =
                new CookieOAuth2AuthorizationRequestRepository(new ObjectMapper(), true);
        MockHttpServletRequest request = new MockHttpServletRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();

        secureRepo.saveAuthorizationRequest(buildRequest("s"), request, response);

        assertThat(response.getHeader("Set-Cookie")).containsIgnoringCase("Secure");
    }
}
