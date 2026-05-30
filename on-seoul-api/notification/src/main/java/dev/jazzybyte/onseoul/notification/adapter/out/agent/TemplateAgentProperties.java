package dev.jazzybyte.onseoul.notification.adapter.out.agent;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.util.StringUtils;

@ConfigurationProperties(prefix = "ai.service")
record TemplateAgentProperties(String url, int templateTimeoutSeconds) {
    TemplateAgentProperties {
        if (!StringUtils.hasText(url)) {
            throw new IllegalArgumentException("ai.service.url must be configured");
        }
        if (templateTimeoutSeconds <= 0) {
            throw new IllegalArgumentException("ai.service.template-timeout-seconds는 양수여야 합니다");
        }
    }
}
