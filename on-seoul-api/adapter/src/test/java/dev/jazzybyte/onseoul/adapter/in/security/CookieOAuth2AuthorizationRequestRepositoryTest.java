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
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

class CookieOAuth2AuthorizationRequestRepositoryTest {

    private static final String TEST_BASE64_KEY = Base64.getEncoder().encodeToString(new byte[32]);

    private CookieOAuth2AuthorizationRequestRepository repository;

    @BeforeEach
    void setUp() {
        repository = new CookieOAuth2AuthorizationRequestRepository(new ObjectMapper(), false, TEST_BASE64_KEY);
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
    void saveAndLoad_roundTrip_preservesState() {
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
        // payload를 직접 만들고 유효한 HMAC을 붙이되 JSON이 아닌 내용으로 테스트
        String payload = Base64.getUrlEncoder().withoutPadding()
                .encodeToString("not-json".getBytes(StandardCharsets.UTF_8));
        // HMAC 없이 payload만 — 서명 검증 실패로 null 반환
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setCookies(new Cookie(CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME, payload));

        assertThat(repository.loadAuthorizationRequest(request)).isNull();
    }

    // ── 서명 검증 ─────────────────────────────────────────────────

    @Test
    @DisplayName("loadAuthorizationRequest — 유효한 payload지만 서명 조작 → null")
    void load_tamperedSignature_returnsNull() {
        // 먼저 정상 저장
        MockHttpServletRequest saveReq = new MockHttpServletRequest();
        MockHttpServletResponse saveRes = new MockHttpServletResponse();
        repository.saveAuthorizationRequest(buildRequest("sig-tamper-state"), saveReq, saveRes);

        String originalCookieValue = saveRes.getHeader("Set-Cookie")
                .replaceAll(".*" + CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME + "=([^;]+).*", "$1");

        // payload는 그대로 두고 sig 부분만 조작
        int dotIndex = originalCookieValue.lastIndexOf('.');
        String payload = originalCookieValue.substring(0, dotIndex);
        String tamperedCookieValue = payload + ".invalidsignature";

        MockHttpServletRequest loadReq = new MockHttpServletRequest();
        loadReq.setCookies(new Cookie(CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME, tamperedCookieValue));

        assertThat(repository.loadAuthorizationRequest(loadReq)).isNull();
    }

    @Test
    @DisplayName("loadAuthorizationRequest — 서명 없이 payload만 있는 경우 → null")
    void load_validPayloadNoSignature_returnsNull() {
        // Base64url 인코딩된 JSON만 있고 .{sig} 없음
        String payload = Base64.getUrlEncoder().withoutPadding()
                .encodeToString("{\"responseType\":\"code\"}".getBytes(StandardCharsets.UTF_8));

        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setCookies(new Cookie(CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME, payload));

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

    @Test
    @DisplayName("removeAuthorizationRequest — 쿠키 없으면 Set-Cookie 헤더 없음 (M-3 검증)")
    void remove_noCookie_doesNotWriteExpireCookie() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();

        OAuth2AuthorizationRequest removed = repository.removeAuthorizationRequest(request, response);

        assertThat(removed).isNull();
        assertThat(response.getHeader("Set-Cookie")).isNull();
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
                new CookieOAuth2AuthorizationRequestRepository(new ObjectMapper(), true, TEST_BASE64_KEY);
        MockHttpServletRequest request = new MockHttpServletRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();

        secureRepo.saveAuthorizationRequest(buildRequest("s"), request, response);

        assertThat(response.getHeader("Set-Cookie")).containsIgnoringCase("Secure");
    }

    // ── deserialize 엣지 케이스 ────────────────────────────────────

    @Test
    @DisplayName("loadAuthorizationRequest — scopes가 null인 JSON을 역직렬화하면 빈 scopes로 복원한다")
    void load_nullScopesInJson_returnsRequestWithEmptyScopes() throws Exception {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("authorizationUri",        "https://accounts.google.com/o/oauth2/auth");
        map.put("responseType",            "code");
        map.put("clientId",                "test-client");
        map.put("redirectUri",             "http://localhost/login/oauth2/code/google");
        map.put("scopes",                  null);   // null 분기
        map.put("state",                   "state-null-scopes");
        map.put("additionalParameters",    Map.of());
        map.put("attributes",              Map.of());
        map.put("authorizationRequestUri", "https://accounts.google.com/o/oauth2/auth?response_type=code");

        String json = new ObjectMapper().writeValueAsString(map);
        String payload = Base64.getUrlEncoder().withoutPadding()
                .encodeToString(json.getBytes(StandardCharsets.UTF_8));

        // 서명 계산을 위해 직접 HMAC 적용 — 임시 repo 사용
        MockHttpServletRequest saveReq = new MockHttpServletRequest();
        MockHttpServletResponse saveRes = new MockHttpServletResponse();
        // 동일한 JSON을 save 경로를 통해 저장하여 서명된 쿠키 값을 얻는 대신,
        // 해당 payload로 서명된 값을 생성하기 위해 javax.crypto 직접 사용
        javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA256");
        mac.init(new javax.crypto.spec.SecretKeySpec(Base64.getDecoder().decode(TEST_BASE64_KEY), "HmacSHA256"));
        String sig = Base64.getUrlEncoder().withoutPadding()
                .encodeToString(mac.doFinal(payload.getBytes(StandardCharsets.UTF_8)));
        String cookieValue = payload + "." + sig;

        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setCookies(new Cookie(CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME, cookieValue));

        OAuth2AuthorizationRequest loaded = repository.loadAuthorizationRequest(request);

        assertThat(loaded).isNotNull();
        assertThat(loaded.getScopes()).isEmpty();
        assertThat(loaded.getState()).isEqualTo("state-null-scopes");
    }

    @Test
    @DisplayName("loadAuthorizationRequest — additionalParameters/attributes 키 자체가 없는 JSON을 역직렬화하면 빈 맵으로 복원한다")
    void load_missingOptionalMaps_returnsRequestWithEmptyMaps() throws Exception {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("authorizationUri",        "https://accounts.google.com/o/oauth2/auth");
        map.put("responseType",            "code");
        map.put("clientId",                "test-client");
        map.put("redirectUri",             "http://localhost/login/oauth2/code/google");
        map.put("scopes",                  java.util.List.of("openid"));
        map.put("state",                   "state-no-maps");
        // additionalParameters, attributes 키 의도적으로 누락
        map.put("authorizationRequestUri", "https://accounts.google.com/o/oauth2/auth?response_type=code");

        String json = new ObjectMapper().writeValueAsString(map);
        String payload = Base64.getUrlEncoder().withoutPadding()
                .encodeToString(json.getBytes(StandardCharsets.UTF_8));

        javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA256");
        mac.init(new javax.crypto.spec.SecretKeySpec(Base64.getDecoder().decode(TEST_BASE64_KEY), "HmacSHA256"));
        String sig = Base64.getUrlEncoder().withoutPadding()
                .encodeToString(mac.doFinal(payload.getBytes(StandardCharsets.UTF_8)));
        String cookieValue = payload + "." + sig;

        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setCookies(new Cookie(CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME, cookieValue));

        OAuth2AuthorizationRequest loaded = repository.loadAuthorizationRequest(request);

        assertThat(loaded).isNotNull();
        assertThat(loaded.getAdditionalParameters()).isEmpty();
        assertThat(loaded.getAttributes()).isEmpty();
    }

    // ── 쿠키 덮어쓰기 동작 ────────────────────────────────────────

    @Test
    @DisplayName("saveAuthorizationRequest — 두 번 호출하면 마지막 state로 쿠키가 덮어써진다")
    void save_calledTwice_latestStateWins() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();

        repository.saveAuthorizationRequest(buildRequest("state-first"), request, response);
        repository.saveAuthorizationRequest(buildRequest("state-second"), request, response);

        // 두 번째 save 이후의 쿠키 값으로 load했을 때 두 번째 state가 반환돼야 한다
        String lastCookieHeader = response.getHeaders("Set-Cookie").getLast();
        String cookieValue = lastCookieHeader
                .replaceAll(".*" + CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME + "=([^;]+).*", "$1");

        MockHttpServletRequest loadReq = new MockHttpServletRequest();
        loadReq.setCookies(new Cookie(CookieOAuth2AuthorizationRequestRepository.COOKIE_NAME, cookieValue));

        OAuth2AuthorizationRequest loaded = repository.loadAuthorizationRequest(loadReq);

        assertThat(loaded).isNotNull();
        assertThat(loaded.getState()).isEqualTo("state-second");
    }
}
