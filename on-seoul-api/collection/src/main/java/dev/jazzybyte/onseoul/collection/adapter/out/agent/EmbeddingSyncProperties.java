package dev.jazzybyte.onseoul.collection.adapter.out.agent;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.util.StringUtils;

/**
 * AI 서비스 호출 공통 설정({@code ai.service.*}).
 *
 * <p>{@code url}은 임베딩 동기화·캐시 flush가 공유한다.
 * {@code adminInternalToken}은 내부 관리 엔드포인트(예: {@code POST /admin/cache/flush})의
 * X-Internal-Token 인증에 쓰이는 공유 시크릿이다 — 소스/로그에 평문으로 남기지 않는다(env 주입).
 *
 * @param url AI 서비스 baseUrl
 * @param embeddingSyncTimeoutSeconds 임베딩 동기화 호출 타임아웃(초)
 * @param adminInternalToken 내부 관리 엔드포인트 X-Internal-Token 공유 시크릿
 * @param adminCacheFlushTimeoutSeconds 캐시 flush 호출 타임아웃(초)
 */
@ConfigurationProperties(prefix = "ai.service")
record EmbeddingSyncProperties(String url,
                               int embeddingSyncTimeoutSeconds,
                               String adminInternalToken,
                               int adminCacheFlushTimeoutSeconds) {
    EmbeddingSyncProperties {
        if (!StringUtils.hasText(url)) {
            throw new IllegalArgumentException("ai.service.url must be configured");
        }
        if (embeddingSyncTimeoutSeconds <= 0) {
            throw new IllegalArgumentException("ai.service.embedding-sync-timeout-seconds는 양수여야 합니다");
        }
        if (adminCacheFlushTimeoutSeconds <= 0) {
            throw new IllegalArgumentException("ai.service.admin-cache-flush-timeout-seconds는 양수여야 합니다");
        }
    }
}
