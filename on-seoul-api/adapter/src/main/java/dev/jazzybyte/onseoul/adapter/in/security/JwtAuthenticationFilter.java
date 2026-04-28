package dev.jazzybyte.onseoul.adapter.in.security;

import dev.jazzybyte.onseoul.domain.port.out.TokenIssuerPort;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.util.StringUtils;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.WebUtils;

import java.io.IOException;
import java.util.Collections;
import java.util.Optional;

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
        String token = resolveToken(request);

        if (StringUtils.hasText(token)) {
            Optional<Long> userIdOpt = tokenIssuerPort.extractUserIdSafely(token);
            if (userIdOpt.isPresent()) {
                Long userId = userIdOpt.get();
                UsernamePasswordAuthenticationToken authentication =
                        new UsernamePasswordAuthenticationToken(userId, null, Collections.emptyList());
                SecurityContextHolder.getContext().setAuthentication(authentication);
                request.setAttribute("userId", userId);
            } else {
                SecurityContextHolder.clearContext();
            }
        }

        filterChain.doFilter(request, response);
    }

    private String resolveToken(HttpServletRequest request) {
        // 1순위: Authorization: Bearer 헤더 (API/모바일 클라이언트)
        String bearerToken = request.getHeader(AUTHORIZATION_HEADER);
        if (StringUtils.hasText(bearerToken) && bearerToken.startsWith(BEARER_PREFIX)) {
            return bearerToken.substring(BEARER_PREFIX.length());
        }
        // 2순위: access_token HttpOnly 쿠키 (브라우저/SPA 클라이언트)
        jakarta.servlet.http.Cookie cookie =
                WebUtils.getCookie(request, OAuth2LoginSuccessHandler.ACCESS_TOKEN_COOKIE);
        return cookie != null ? cookie.getValue() : null;
    }
}
