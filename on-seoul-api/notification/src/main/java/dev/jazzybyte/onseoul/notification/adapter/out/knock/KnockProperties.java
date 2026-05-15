package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.util.StringUtils;

@ConfigurationProperties(prefix = "knock")
record KnockProperties(String apiKey, String emailWorkflowKey, String smsWorkflowKey, int timeoutSeconds) {

    KnockProperties {
        if (!StringUtils.hasText(apiKey)) {
            throw new IllegalArgumentException("knock.api-key 미설정");
        }
        if (timeoutSeconds <= 0) {
            throw new IllegalArgumentException("knock.timeout-seconds는 양수여야 합니다");
        }
    }
}
