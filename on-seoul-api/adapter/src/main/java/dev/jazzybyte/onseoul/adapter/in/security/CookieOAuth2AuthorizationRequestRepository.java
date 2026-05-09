package dev.jazzybyte.onseoul.adapter.in.security;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseCookie;
import org.springframework.security.oauth2.client.web.AuthorizationRequestRepository;
import org.springframework.security.oauth2.core.endpoint.OAuth2AuthorizationRequest;
import org.springframework.security.oauth2.core.endpoint.OAuth2AuthorizationResponseType;
import org.springframework.stereotype.Component;
import org.springframework.web.util.WebUtils;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * OAuth2 인증 요청(state 포함)을 HttpOnly 쿠키에 저장하는 Stateless 구현.
 *
 * <h3>도입 배경</h3>
 * <p>기본 구현인 {@link org.springframework.security.oauth2.client.web.HttpSessionOAuth2AuthorizationRequestRepository}는
 * state를 서버 세션에 저장한다. 분산 환경에서 콜백 요청이 다른 서버로 라우팅되면
 * state 검증에 실패해 인증이 깨지는 문제가 있다.</p>
 *
 * <h3>쿠키 직렬화 방식</h3>
 * <p>{@link OAuth2AuthorizationRequest}의 필드를 JSON으로 직렬화한 뒤 Base64url 인코딩하고
 * HMAC-SHA256 서명을 붙여 {@code {payload}.{sig}} 형태로 쿠키에 저장한다.
 * 인증 완료 후({@link #removeAuthorizationRequest}) 쿠키를 즉시 만료시킨다.</p>
 *
 * <h3>SameSite=Lax 사유</h3>
 * <p>OAuth2 콜백은 외부 IdP(Google, Kakao)에서 리다이렉트되므로 cross-site 요청이다.
 * {@code SameSite=Strict}이면 콜백 쿠키가 전송되지 않아 인증이 실패한다.
 * {@code Lax}는 GET 리다이렉트는 허용하므로 OAuth2 콜백 흐름과 호환된다.</p>
 */
@Slf4j
@Component
public class CookieOAuth2AuthorizationRequestRepository
        implements AuthorizationRequestRepository<OAuth2AuthorizationRequest> {

    static final String COOKIE_NAME = "oauth2_auth_request";
    private static final int COOKIE_MAX_AGE_SECONDS = 600; // 10분 — OAuth2 플로우 완료에 충분
    private static final String HMAC_ALGORITHM = "HmacSHA256";

    private final ObjectMapper objectMapper;
    private final boolean cookieSecure;
    private final byte[] signingKeyBytes;

    public CookieOAuth2AuthorizationRequestRepository(
            final ObjectMapper objectMapper,
            @Value("${app.cookie-secure:true}") boolean cookieSecure,
            @Value("${app.cookie-signing-key}") String base64SigningKey) {
        this.objectMapper = objectMapper;
        this.cookieSecure = cookieSecure;
        this.signingKeyBytes = Base64.getDecoder().decode(base64SigningKey);
    }

    @Override
    public OAuth2AuthorizationRequest loadAuthorizationRequest(HttpServletRequest request) {
        Cookie cookie = WebUtils.getCookie(request, COOKIE_NAME);
        if (cookie == null) {
            return null;
        }
        String cookieValue = cookie.getValue();
        int dotIndex = cookieValue.lastIndexOf('.');
        if (dotIndex < 0) {
            log.warn("oauth2 쿠키 서명 검증 실패");
            return null;
        }
        String payload = cookieValue.substring(0, dotIndex);
        String sig = cookieValue.substring(dotIndex + 1);

        try {
            String expectedSig = computeHmac(payload);
            if (!MessageDigest.isEqual(
                    expectedSig.getBytes(StandardCharsets.UTF_8),
                    sig.getBytes(StandardCharsets.UTF_8))) {
                log.warn("oauth2 쿠키 서명 검증 실패");
                return null;
            }
            String json = new String(Base64.getUrlDecoder().decode(payload), StandardCharsets.UTF_8);
            return deserialize(json);
        } catch (IllegalArgumentException e) {
            log.warn("oauth2 쿠키 Base64 디코딩 실패: {}", e.getMessage());
            return null;
        } catch (JsonProcessingException e) {
            log.warn("oauth2 쿠키 JSON 역직렬화 실패: {}", e.getMessage());
            return null;
        } catch (Exception e) {
            log.warn("oauth2 쿠키 처리 중 예상치 못한 오류: {}", e.getMessage());
            return null;
        }
    }

    @Override
    public void saveAuthorizationRequest(OAuth2AuthorizationRequest authorizationRequest,
                                         HttpServletRequest request,
                                         HttpServletResponse response) {
        if (authorizationRequest == null) {
            expireCookie(response);
            return;
        }
        try {
            String json = objectMapper.writeValueAsString(toMap(authorizationRequest));
            String payload = Base64.getUrlEncoder().withoutPadding()
                    .encodeToString(json.getBytes(StandardCharsets.UTF_8));
            String sig = computeHmac(payload);
            response.addHeader(HttpHeaders.SET_COOKIE,
                    buildCookie(payload + "." + sig, COOKIE_MAX_AGE_SECONDS).toString());
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("OAuth2 authorization request 직렬화 실패", e);
        }
    }

    @Override
    public OAuth2AuthorizationRequest removeAuthorizationRequest(HttpServletRequest request,
                                                                  HttpServletResponse response) {
        OAuth2AuthorizationRequest saved = loadAuthorizationRequest(request);
        if (saved != null) {
            expireCookie(response);
        }
        return saved;
    }

    // ── 내부 헬퍼 ──────────────────────────────────────────────────

    private String computeHmac(String payload) {
        try {
            Mac mac = Mac.getInstance(HMAC_ALGORITHM);
            mac.init(new SecretKeySpec(signingKeyBytes, HMAC_ALGORITHM));
            byte[] hmacBytes = mac.doFinal(payload.getBytes(StandardCharsets.UTF_8));
            return Base64.getUrlEncoder().withoutPadding().encodeToString(hmacBytes);
        } catch (Exception e) {
            throw new IllegalStateException("HMAC-SHA256 서명 계산 실패", e);
        }
    }

    private void expireCookie(HttpServletResponse response) {
        response.addHeader(HttpHeaders.SET_COOKIE, buildCookie("", 0).toString());
    }

    private ResponseCookie buildCookie(String value, int maxAge) {
        return ResponseCookie.from(COOKIE_NAME, value)
                .httpOnly(true)
                .secure(cookieSecure)
                // Lax: GET 리다이렉트(OAuth2 콜백)는 허용, POST cross-site는 차단
                .sameSite("Lax")
                .path("/")
                .maxAge(maxAge)
                .build();
    }

    private Map<String, Object> toMap(OAuth2AuthorizationRequest req) {
        Map<String, Object> filteredAttributes = req.getAttributes().entrySet().stream()
                .filter(e -> !"code_verifier".equals(e.getKey()))
                .collect(Collectors.toMap(Map.Entry::getKey, Map.Entry::getValue,
                        (a, b) -> a, LinkedHashMap::new));

        Map<String, Object> map = new LinkedHashMap<>();
        map.put("authorizationUri",        req.getAuthorizationUri());
        map.put("responseType",            req.getResponseType().getValue());
        map.put("clientId",                req.getClientId());
        map.put("redirectUri",             req.getRedirectUri());
        map.put("scopes",                  req.getScopes());
        map.put("state",                   req.getState());
        map.put("additionalParameters",    req.getAdditionalParameters());
        map.put("attributes",              filteredAttributes);
        map.put("authorizationRequestUri", req.getAuthorizationRequestUri());
        return map;
    }

    @SuppressWarnings("unchecked")
    private OAuth2AuthorizationRequest deserialize(String json) throws JsonProcessingException {
        Map<String, Object> map = objectMapper.readValue(
                json, new TypeReference<Map<String, Object>>() {});

        String responseType = (String) map.get("responseType");
        if (!OAuth2AuthorizationResponseType.CODE.getValue().equals(responseType)) {
            throw new IllegalStateException("지원하지 않는 responseType: " + responseType);
        }
        OAuth2AuthorizationRequest.Builder builder = OAuth2AuthorizationRequest.authorizationCode();

        List<?> rawScopes = (List<?>) map.get("scopes");
        LinkedHashSet<String> scopes = new LinkedHashSet<>();
        if (rawScopes != null) {
            rawScopes.forEach(s -> scopes.add(s.toString()));
        }

        return builder
                .authorizationUri((String) map.get("authorizationUri"))
                .clientId((String) map.get("clientId"))
                .redirectUri((String) map.get("redirectUri"))
                .scopes(scopes)
                .state((String) map.get("state"))
                .additionalParameters((Map<String, Object>) map.getOrDefault("additionalParameters", Map.of()))
                .attributes((Map<String, Object>) map.getOrDefault("attributes", Map.of()))
                .authorizationRequestUri((String) map.get("authorizationRequestUri"))
                .build();
    }
}
