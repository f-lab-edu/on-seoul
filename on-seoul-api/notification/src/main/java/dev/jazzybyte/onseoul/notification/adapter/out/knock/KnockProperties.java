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

    /**
     * API 키를 로그·직렬화에서 보호한다.
     * Java record는 기본 toString()에 모든 필드를 포함하므로 명시적으로 override한다.
     *
     * TODO(key-rotation): 현재 키 교체는 앱 재시작이 필요하다.
     *   Phase 2에서 Spring Cloud Config 또는 Vault Dynamic Secret으로 hot reload 지원 검토.
     */
    @Override
    public String toString() {
        return "KnockProperties[apiKey=[PROTECTED]"
                + ", emailWorkflowKey=" + emailWorkflowKey
                + ", smsWorkflowKey=" + smsWorkflowKey
                + ", timeoutSeconds=" + timeoutSeconds + "]";
    }
}
