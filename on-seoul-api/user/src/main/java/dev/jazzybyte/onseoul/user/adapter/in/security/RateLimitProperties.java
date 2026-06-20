package dev.jazzybyte.onseoul.user.adapter.in.security;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 사용자 기준 RPM(분당 요청 수) 레이트 리밋 설정.
 *
 * <p>레이트 리밋은 보안 횡단관심사이므로 user 모듈(SecurityConfig·JwtAuthenticationFilter가 있는 곳)에
 * 둔다. 대상 경로/RPM을 설정값으로 외부화해 user 모듈이 chat 모듈을 직접 의존하지 않게 한다.</p>
 *
 * <ul>
 *   <li>{@code enabled} — 레이트 리밋 활성화 여부. false면 필터는 통과만 한다.</li>
 *   <li>{@code requestsPerMinute} — 윈도우 내 허용 요청 수.</li>
 *   <li>{@code windowSeconds} — 슬라이딩 윈도우 크기(초).</li>
 *   <li>{@code path} — 레이트 리밋 대상 경로(POST). 그 외 경로는 미적용.</li>
 * </ul>
 */
@ConfigurationProperties(prefix = "app.rate-limit")
public record RateLimitProperties(
        boolean enabled,
        int requestsPerMinute,
        int windowSeconds,
        String path
) {
    public RateLimitProperties {
        if (requestsPerMinute <= 0) requestsPerMinute = 20;
        if (windowSeconds <= 0) windowSeconds = 60;
        if (path == null || path.isBlank()) path = "/api/chat/query";
    }
}
