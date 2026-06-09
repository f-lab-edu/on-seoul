package dev.jazzybyte.onseoul.user.adapter.in.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;

import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;

@ExtendWith(MockitoExtension.class)
class RateLimitFilterTest {

    private static final String TARGET_PATH = "/api/chat/query";

    @Mock
    private SlidingWindowRateLimiter rateLimiter;

    @Mock
    private FilterChain chain;

    private final ObjectMapper objectMapper = new ObjectMapper();

    private RateLimitFilter newFilter(boolean enabled) {
        RateLimitProperties properties = new RateLimitProperties(enabled, 20, 60, TARGET_PATH);
        return new RateLimitFilter(rateLimiter, properties, objectMapper);
    }

    private void authenticate(long userId) {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken(userId, null, Collections.emptyList()));
    }

    @BeforeEach
    void setUp() {
        SecurityContextHolder.clearContext();
    }

    @AfterEach
    void tearDown() {
        SecurityContextHolder.clearContext();
    }

    @Test
    @DisplayName("인증된 사용자가 한도 내면 통과한다")
    void allowsWithinLimit() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", TARGET_PATH);
        MockHttpServletResponse response = new MockHttpServletResponse();
        authenticate(42L);
        given(rateLimiter.tryAcquire(anyLong(), anyInt(), anyInt())).willReturn(true);

        newFilter(true).doFilter(request, response, chain);

        assertThat(response.getStatus()).isEqualTo(200);
        verify(chain).doFilter(request, response);
    }

    @Test
    @DisplayName("limiter에 인증 userId와 설정된 RPM/윈도우 값을 그대로 전달한다")
    void passesConfiguredArgsToLimiter() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", TARGET_PATH);
        MockHttpServletResponse response = new MockHttpServletResponse();
        authenticate(99L);
        given(rateLimiter.tryAcquire(anyLong(), anyInt(), anyInt())).willReturn(true);

        newFilter(true).doFilter(request, response, chain);

        verify(rateLimiter).tryAcquire(eq(99L), eq(20), eq(60));
    }

    @Test
    @DisplayName("한도 초과 시 429와 {code,message} JSON을 반환하고 체인을 중단한다")
    void blocksWhenExceeded() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", TARGET_PATH);
        MockHttpServletResponse response = new MockHttpServletResponse();
        authenticate(42L);
        given(rateLimiter.tryAcquire(anyLong(), anyInt(), anyInt())).willReturn(false);

        newFilter(true).doFilter(request, response, chain);

        assertThat(response.getStatus()).isEqualTo(429);
        assertThat(response.getContentType()).contains("application/json");
        assertThat(response.getCharacterEncoding().toUpperCase()).contains("UTF-8");
        Map<?, ?> body = objectMapper.readValue(response.getContentAsString(), Map.class);
        assertThat(body.get("code")).isEqualTo("RATE_LIMIT_EXCEEDED");
        assertThat(body.get("message")).isEqualTo("요청이 너무 잦습니다. 잠시 후 다시 시도해주세요.");
        verify(chain, never()).doFilter(request, response);
    }

    @Test
    @DisplayName("enabled=false면 레이트 리밋을 적용하지 않고 통과한다")
    void passesWhenDisabled() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", TARGET_PATH);
        MockHttpServletResponse response = new MockHttpServletResponse();
        authenticate(42L);

        newFilter(false).doFilter(request, response, chain);

        verify(chain).doFilter(request, response);
        verifyNoInteractions(rateLimiter);
    }

    @Test
    @DisplayName("대상 경로가 아니면 레이트 리밋을 적용하지 않는다")
    void passesNonTargetPath() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/chat/rooms");
        MockHttpServletResponse response = new MockHttpServletResponse();
        authenticate(42L);

        newFilter(true).doFilter(request, response, chain);

        verify(chain).doFilter(request, response);
        verifyNoInteractions(rateLimiter);
    }

    @Test
    @DisplayName("미인증 요청은 레이트 리밋을 적용하지 않고 통과한다(Security가 401 처리)")
    void passesWhenUnauthenticated() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", TARGET_PATH);
        MockHttpServletResponse response = new MockHttpServletResponse();

        newFilter(true).doFilter(request, response, chain);

        verify(chain).doFilter(request, response);
        verifyNoInteractions(rateLimiter);
    }

    @Test
    @DisplayName("principal이 Long이 아니면(예: anonymousUser String) 레이트 리밋을 적용하지 않고 통과한다")
    void passesWhenPrincipalNotLong() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", TARGET_PATH);
        MockHttpServletResponse response = new MockHttpServletResponse();
        // Security의 익명 인증처럼 principal이 String인 경우 — userId 추출 실패 → 통과
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken("anonymousUser", null, Collections.emptyList()));

        newFilter(true).doFilter(request, response, chain);

        verify(chain).doFilter(request, response);
        verifyNoInteractions(rateLimiter);
    }

    @Test
    @DisplayName("429 바디가 UTF-8 바이트로 한글 깨짐 없이 직렬화된다")
    void blockBodyIsValidUtf8() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", TARGET_PATH);
        MockHttpServletResponse response = new MockHttpServletResponse();
        authenticate(42L);
        given(rateLimiter.tryAcquire(anyLong(), anyInt(), anyInt())).willReturn(false);

        newFilter(true).doFilter(request, response, chain);

        // 응답 원시 바이트를 UTF-8로 디코딩했을 때 한글이 그대로 살아있어야 한다(mojibake 없음).
        byte[] raw = response.getContentAsByteArray();
        String decoded = new String(raw, StandardCharsets.UTF_8);
        assertThat(decoded).contains("요청이 너무 잦습니다. 잠시 후 다시 시도해주세요.");
        assertThat(decoded).contains("RATE_LIMIT_EXCEEDED");
        // 한글 메시지는 멀티바이트이므로 바이트 길이가 문자 길이보다 길어야 한다(US-ASCII 직렬화 방지 회귀).
        assertThat(raw.length).isGreaterThan(decoded.length());
    }

    @Test
    @DisplayName("대상 경로면 HTTP 메서드와 무관하게 레이트 리밋이 적용된다(URI 기준 매칭 — 동작 고정)")
    void appliesByUriRegardlessOfMethod() throws Exception {
        // 설계상 대상은 POST /api/chat/query 지만 필터는 URI만 비교한다.
        // 현재 동작을 회귀로 고정한다: 동일 URI의 GET도 limiter를 거친다.
        MockHttpServletRequest request = new MockHttpServletRequest("GET", TARGET_PATH);
        MockHttpServletResponse response = new MockHttpServletResponse();
        authenticate(42L);
        given(rateLimiter.tryAcquire(anyLong(), anyInt(), anyInt())).willReturn(true);

        newFilter(true).doFilter(request, response, chain);

        verify(rateLimiter).tryAcquire(anyLong(), anyInt(), anyInt());
        verify(chain).doFilter(request, response);
    }
}
