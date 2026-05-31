package dev.jazzybyte.onseoul.collection.adapter.out.agent;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.util.StringUtils;

@ConfigurationProperties(prefix = "ai.service")
record EmbeddingSyncProperties(String url, int embeddingSyncTimeoutSeconds) {
    EmbeddingSyncProperties {
        if (!StringUtils.hasText(url)) {
            throw new IllegalArgumentException("ai.service.url must be configured");
        }
        if (embeddingSyncTimeoutSeconds <= 0) {
            throw new IllegalArgumentException("ai.service.embedding-sync-timeout-seconds는 양수여야 합니다");
        }
    }
}
