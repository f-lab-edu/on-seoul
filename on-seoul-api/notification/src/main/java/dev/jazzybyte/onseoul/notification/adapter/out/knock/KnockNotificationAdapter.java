package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Knock REST API를 통해 알림을 발송한다.
 *
 * <p>recipients 필드에 email/phone_number를 포함한 인라인 식별 정보를 전달하므로
 * Knock에 수신자를 사전 등록하지 않아도 자동으로 upsert된다.</p>
 *
 * <p>EMAIL/SMS 각 채널은 별도 Knock 워크플로우로 트리거된다.
 * 하나 채널 실패 시 다른 채널 트리거를 계속 시도한다.
 * 모든 채널이 실패하면 RuntimeException을 던진다.</p>
 */
@Slf4j
@Component("knockPrimary")
class KnockNotificationAdapter implements PushNotificationPort {

    private final WebClient knockWebClient;
    private final KnockProperties props;

    KnockNotificationAdapter(@Qualifier("knockWebClient") WebClient knockWebClient,
                              KnockProperties props) {
        this.knockWebClient = knockWebClient;
        this.props = props;
    }

    @Override
    public void send(UserContact recipient, String title, String body, Long dispatchId,
                     Set<NotificationChannel> channels) {
        if (channels == null || channels.isEmpty()) {
            log.warn("[Knock] channels가 비어있어 발송 스킵: userId={}, dispatchId={}",
                    recipient.userId(), dispatchId);
            return;
        }

        int failCount = 0;

        for (NotificationChannel channel : channels) {
            if (!hasRequiredContact(recipient, channel)) {
                log.warn("[Knock] {} 채널 발송 스킵 — 연락처 미등록: userId={}, dispatchId={}",
                        channel, recipient.userId(), dispatchId);
                failCount++;
                continue;
            }

            String workflowKey = resolveWorkflowKey(channel);
            try {
                triggerWorkflow(workflowKey, recipient, title, body, dispatchId);
                log.info("[Knock] 발송 성공: userId={}, channel={}, dispatchId={}",
                        recipient.userId(), channel, dispatchId);
            } catch (Exception ex) {
                failCount++;
                log.warn("[Knock] 발송 실패: userId={}, channel={}, dispatchId={}, error={}",
                        recipient.userId(), channel, dispatchId, ex.getMessage());
            }
        }

        if (failCount > 0 && failCount == channels.size()) {
            throw new RuntimeException(
                    String.format("[Knock] 모든 채널 발송 실패: userId=%d, dispatchId=%d",
                            recipient.userId(), dispatchId));
        }
    }

    /**
     * 채널별로 필요한 연락처가 등록되어 있는지 확인한다.
     */
    private boolean hasRequiredContact(UserContact recipient, NotificationChannel channel) {
        return switch (channel) {
            case EMAIL -> StringUtils.hasText(recipient.email());
            case SMS   -> StringUtils.hasText(recipient.phoneNumber());
        };
    }

    private String resolveWorkflowKey(NotificationChannel channel) {
        return switch (channel) {
            case EMAIL -> props.emailWorkflowKey();
            case SMS   -> props.smsWorkflowKey();
        };
    }

    /**
     * Knock 워크플로우를 트리거한다.
     * recipients에 email/phone_number를 포함하여 Knock이 수신자를 인라인으로 upsert하게 한다.
     */
    private void triggerWorkflow(String workflowKey, UserContact recipient,
                                 String title, String body, Long dispatchId) {
        Map<String, Object> recipientMap = new LinkedHashMap<>();
        recipientMap.put("id", String.valueOf(recipient.userId()));
        if (StringUtils.hasText(recipient.email())) {
            recipientMap.put("email", recipient.email());
        }
        if (StringUtils.hasText(recipient.phoneNumber())) {
            recipientMap.put("phone_number", recipient.phoneNumber());
        }

        Map<String, Object> requestBody = Map.of(
                "recipients", List.of(recipientMap),
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
