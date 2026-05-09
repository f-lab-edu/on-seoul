package dev.jazzybyte.onseoul.security.jwt;

import dev.jazzybyte.onseoul.security.OAuth2LoginSuccessHandler;
import jakarta.servlet.FilterChain;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.core.context.SecurityContextHolder;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class JwtAuthenticationFilterTest {

    @Mock
    private JwtProvider jwtProvider;

    @Mock
    private FilterChain filterChain;

    private JwtAuthenticationFilter filter;

    @BeforeEach
    void setUp() {
        filter = new JwtAuthenticationFilter(jwtProvider);
        SecurityContextHolder.clearContext();
    }

    @Test
    @DisplayName("유효한 Bearer 토큰이 있으면 SecurityContext에 인증 정보를 설정한다")
    void doFilterInternal_validToken_setsAuthentication() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("Authorization", "Bearer valid-token");
        MockHttpServletResponse response = new MockHttpServletResponse();

        when(jwtProvider.extractUserIdSafely("valid-token")).thenReturn(Optional.of(42L));

        filter.doFilter(request, response, filterChain);

        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNotNull();
        assertThat(SecurityContextHolder.getContext().getAuthentication().getPrincipal()).isEqualTo(42L);
        assertThat(request.getAttribute("userId")).isEqualTo(42L);
        verify(filterChain).doFilter(request, response);
    }

    @Test
    @DisplayName("Authorization 헤더가 없으면 SecurityContext에 인증 정보를 설정하지 않는다")
    void doFilterInternal_noHeader_doesNotSetAuthentication() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();

        filter.doFilter(request, response, filterChain);

        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNull();
        verify(jwtProvider, never()).extractUserIdSafely(any());
        verify(filterChain).doFilter(request, response);
    }

    @Test
    @DisplayName("Bearer 접두사가 없는 헤더는 무시한다")
    void doFilterInternal_noBearerPrefix_doesNotSetAuthentication() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("Authorization", "Basic dXNlcjpwYXNz");
        MockHttpServletResponse response = new MockHttpServletResponse();

        filter.doFilter(request, response, filterChain);

        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNull();
        verify(jwtProvider, never()).extractUserIdSafely(any());
        verify(filterChain).doFilter(request, response);
    }

    @Test
    @DisplayName("유효하지 않은 토큰이면 SecurityContext를 지우고 필터 체인을 계속한다")
    void doFilterInternal_invalidToken_clearsContextAndContinues() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("Authorization", "Bearer bad-token");
        MockHttpServletResponse response = new MockHttpServletResponse();

        when(jwtProvider.extractUserIdSafely("bad-token")).thenReturn(Optional.empty());

        filter.doFilter(request, response, filterChain);

        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNull();
        verify(filterChain).doFilter(request, response);
    }

    @Test
    @DisplayName("만료된 토큰이면 SecurityContext를 지우고 필터 체인을 계속한다")
    void doFilterInternal_expiredToken_clearsContextAndContinues() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("Authorization", "Bearer expired-token");
        MockHttpServletResponse response = new MockHttpServletResponse();

        when(jwtProvider.extractUserIdSafely("expired-token")).thenReturn(Optional.empty());

        filter.doFilter(request, response, filterChain);

        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNull();
        verify(filterChain).doFilter(request, response);
    }

    @Test
    @DisplayName("'Bearer ' 이후 공백만 있는 토큰(빈 문자열)은 무시한다")
    void doFilterInternal_emptyTokenAfterBearer_doesNotSetAuthentication() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("Authorization", "Bearer ");
        MockHttpServletResponse response = new MockHttpServletResponse();

        filter.doFilter(request, response, filterChain);

        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNull();
        verify(jwtProvider, never()).extractUserIdSafely(any());
        verify(filterChain).doFilter(request, response);
    }

    @Test
    @DisplayName("Authorization 헤더 없이 access_token 쿠키가 있으면 쿠키로 인증한다")
    void doFilterInternal_validAccessTokenCookie_setsAuthentication() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.setCookies(new Cookie(OAuth2LoginSuccessHandler.ACCESS_TOKEN_COOKIE, "cookie-token"));
        MockHttpServletResponse response = new MockHttpServletResponse();

        when(jwtProvider.extractUserIdSafely("cookie-token")).thenReturn(Optional.of(77L));

        filter.doFilter(request, response, filterChain);

        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNotNull();
        assertThat(SecurityContextHolder.getContext().getAuthentication().getPrincipal()).isEqualTo(77L);
        assertThat(request.getAttribute("userId")).isEqualTo(77L);
        verify(filterChain).doFilter(request, response);
    }

    @Test
    @DisplayName("Authorization 헤더가 Bearer를 포함하면 쿠키보다 헤더를 우선한다")
    void doFilterInternal_headerTakesPriorityOverCookie() throws Exception {
        MockHttpServletRequest request = new MockHttpServletRequest();
        request.addHeader("Authorization", "Bearer header-token");
        request.setCookies(new Cookie(OAuth2LoginSuccessHandler.ACCESS_TOKEN_COOKIE, "cookie-token"));
        MockHttpServletResponse response = new MockHttpServletResponse();

        when(jwtProvider.extractUserIdSafely("header-token")).thenReturn(Optional.of(10L));

        filter.doFilter(request, response, filterChain);

        verify(jwtProvider).extractUserIdSafely("header-token");
        verify(jwtProvider, never()).extractUserIdSafely("cookie-token");
        verify(filterChain).doFilter(request, response);
    }
}
