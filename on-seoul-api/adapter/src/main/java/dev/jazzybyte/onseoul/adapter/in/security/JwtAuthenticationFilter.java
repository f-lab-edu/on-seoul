package dev.jazzybyte.onseoul.adapter.in.security;

import dev.jazzybyte.onseoul.domain.port.out.TokenIssuerPort;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.Cookie;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingRequestWrapper;
import org.springframework.web.util.WebUtils;

import java.io.IOException;
import java.util.Collections;

public class JwtAuthenticationFilter extends OncePerRequestFilter {

    private static final String AUTHORIZATION_HEADER = "Authorization";
    private static final String BEARER_PREFIX = "Bearer ";

    private final TokenIssuerPort tokenIssuerPort;

    public JwtAuthenticationFilter(final TokenIssuerPort tokenIssuerPort) {
        this.tokenIssuerPort = tokenIssuerPort;
    }

    @Override

    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        // 요청 본문을 여러 번 읽을 수 있도록 ContentCachingRequestWrapper로 래핑
        ContentCachingRequestWrapper wrappedRequest = new ContentCachingRequestWrapper(request);
        // Authorization 헤더 또는 쿠키에서 JWT 토큰 추출
        String token = resolveToken(wrappedRequest);

        if (StringUtils.hasText(token)) {
            tokenIssuerPort.extractUserIdSafely(token).ifPresent(userId -> {
                SecurityContextHolder.getContext().setAuthentication(
                        new UsernamePasswordAuthenticationToken(userId, null, Collections.emptyList()));
                wrappedRequest.setAttribute("userId", userId);
            });
        }

        filterChain.doFilter(wrappedRequest, response);
    }

    private String resolveToken(HttpServletRequest request) {
        // 1순위: Authorization: Bearer 헤더 (API/모바일 클라이언트)
        String bearerToken = request.getHeader(AUTHORIZATION_HEADER);
        if (StringUtils.hasText(bearerToken) && bearerToken.startsWith(BEARER_PREFIX)) {
            return bearerToken.substring(BEARER_PREFIX.length());
        }
        // 2순위: access_token HttpOnly 쿠키 (브라우저/SPA 클라이언트)
        Cookie cookie = WebUtils.getCookie(request, OAuth2LoginSuccessHandler.ACCESS_TOKEN_COOKIE);
        return cookie != null ? cookie.getValue() : null;
    }
}
