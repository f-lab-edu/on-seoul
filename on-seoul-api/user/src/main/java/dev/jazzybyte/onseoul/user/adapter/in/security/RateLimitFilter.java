package dev.jazzybyte.onseoul.user.adapter.in.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/**
 * 사용자 기준 RPM 레이트 리밋 필터.
 *
 * <p>JwtAuthenticationFilter 이후(SecurityContext에 userId가 채워진 뒤)에 동작한다.
 * 대상 경로(POST {@code app.rate-limit.path})에서만 적용하며, 한도 초과 시 429와
 * GlobalExceptionHandler와 동일한 {@code {code,message}} JSON을 직접 작성한다
 * (필터 예외는 @RestControllerAdvice가 가로채지 못하므로 필터에서 직접 작성).</p>
 *
 * <p>ChatConcurrencyGuard(동시 실행 cap)와는 보완 관계다. 이 필터는 분당 호출 수(RPM)를
 * 제한하고, 가드는 동시 진행 중인 생성 수를 제한한다.</p>
 */
@Slf4j
public class RateLimitFilter extends OncePerRequestFilter {

    private final SlidingWindowRateLimiter rateLimiter;
    private final RateLimitProperties properties;
    private final ObjectMapper objectMapper;

    public RateLimitFilter(final SlidingWindowRateLimiter rateLimiter,
                           final RateLimitProperties properties,
                           final ObjectMapper objectMapper) {
        this.rateLimiter = rateLimiter;
        this.properties = properties;
        this.objectMapper = objectMapper;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request,
                                    HttpServletResponse response,
                                    FilterChain filterChain) throws ServletException, IOException {
        if (!properties.enabled() || !isTarget(request)) {
            filterChain.doFilter(request, response);
            return;
        }

        Long userId = resolveUserId();
        if (userId == null) {
            // 미인증 — Security 인가 단계가 401을 처리한다.
            filterChain.doFilter(request, response);
            return;
        }

        boolean allowed = rateLimiter.tryAcquire(
                userId, properties.requestsPerMinute(), properties.windowSeconds());
        if (!allowed) {
            log.warn("[RateLimit] RPM 초과 차단 - userId={}, limit={}/{}s",
                    userId, properties.requestsPerMinute(), properties.windowSeconds());
            writeTooManyRequests(response);
            return;
        }

        filterChain.doFilter(request, response);
    }

    private boolean isTarget(HttpServletRequest request) {
        return properties.path().equals(request.getRequestURI());
    }

    private Long resolveUserId() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth != null && auth.getPrincipal() instanceof Long userId) {
            return userId;
        }
        return null;
    }

    private void writeTooManyRequests(HttpServletResponse response) throws IOException {
        ErrorCode code = ErrorCode.RATE_LIMIT_EXCEEDED;
        response.setStatus(code.getHttpStatus());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        objectMapper.writeValue(response.getWriter(),
                Map.of("code", code.getCode(), "message", code.getDefaultMessage()));
    }
}
