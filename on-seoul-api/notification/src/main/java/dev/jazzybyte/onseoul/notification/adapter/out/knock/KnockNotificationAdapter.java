package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Knock REST API를 통해 알림을 발송한다.
 *
 * <p>EMAIL/SMS 각 채널은 별도 Knock 워크플로우로 트리거된다.
 * 하나 채널 실패 시 다른 채널 트리거를 계속 시도한다.
 * 모든 채널이 실패하면 RuntimeException을 던진다.</p>
 */
@Slf4j
@Component
class KnockNotificationAdapter implements PushNotificationPort {

    private final WebClient knockWebClient;
    private final KnockProperties props;

    KnockNotificationAdapter(@Qualifier("knockWebClient") WebClient knockWebClient,
                              KnockProperties props) {
        this.knockWebClient = knockWebClient;
        this.props = props;
    }

    @Override
    public void send(Long userId, String title, String body, Long dispatchId,
                     Set<NotificationChannel> channels) {
        if (channels == null || channels.isEmpty()) {
            log.warn("[Knock] channels가 비어있어 발송 스킵: userId={}, dispatchId={}", userId, dispatchId);
            return;
        }

        int failCount = 0;

        for (NotificationChannel channel : channels) {
            String workflowKey = resolveWorkflowKey(channel);
            try {
                triggerWorkflow(workflowKey, userId, title, body, dispatchId);
                log.info("[Knock] 발송 성공: userId={}, channel={}, dispatchId={}", userId, channel, dispatchId);
            } catch (Exception ex) {
                failCount++;
                log.warn("[Knock] 발송 실패: userId={}, channel={}, dispatchId={}, error={}",
                        userId, channel, dispatchId, ex.getMessage());
            }
        }

        if (failCount > 0 && failCount == channels.size()) {
            throw new RuntimeException(
                    String.format("[Knock] 모든 채널 발송 실패: userId=%d, dispatchId=%d", userId, dispatchId));
        }
    }

    private String resolveWorkflowKey(NotificationChannel channel) {
        return switch (channel) {
            case EMAIL -> props.emailWorkflowKey();
            case SMS -> props.smsWorkflowKey();
        };
    }

    private void triggerWorkflow(String workflowKey, Long userId, String title, String body,
                                 Long dispatchId) {
        Map<String, Object> requestBody = Map.of(
                "recipients", List.of(String.valueOf(userId)),
                "data", Map.of(
                        "title", title,
                        "body", body,
                        "dispatch_id", String.valueOf(dispatchId)
                )
        );

        knockWebClient.post()
                .uri("/v1/workflows/{key}/trigger", workflowKey)
                .bodyValue(requestBody)
                .retrieve()
                .bodyToMono(Void.class)
                .timeout(Duration.ofSeconds(props.timeoutSeconds()))
                .block();
    }
}
