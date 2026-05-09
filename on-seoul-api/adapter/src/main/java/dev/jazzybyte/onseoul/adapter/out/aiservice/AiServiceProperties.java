package dev.jazzybyte.onseoul.adapter.out.aiservice;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.util.StringUtils;

@ConfigurationProperties(prefix = "ai.service")
public record AiServiceProperties(
        String url,
        int streamTimeoutSeconds
) {
    public AiServiceProperties {
        if (!StringUtils.hasText(url)) {
            throw new IllegalArgumentException("ai.service.url must be configured and non-blank");
        }
        if (streamTimeoutSeconds <= 0) streamTimeoutSeconds = 30;
    }
}