package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.FallbackReason;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;

import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeoutException;

/**
 * Knock REST API를 통해 알림을 발송한다.
 *
 * <p>recipients 필드에 email/phone_number를 포함한 인라인 식별 정보를 전달하므로
 * Knock에 수신자를 사전 등록하지 않아도 자동으로 upsert된다.
 *
 * <p><b>PII 보호:</b> 요청 본문 {@code recipients}에 email/phone_number가 평문으로 포함된다.
 * {@link KnockClientConfig}에서 reactor-netty {@code wiretap=false}를 명시하여 HTTP body
 * 로깅 경로를 차단하고 있다. 운영에서 아래 설정을 활성화하면 PII가 노출된다:
 * <ul>
 *   <li>{@code logging.level.reactor.netty=DEBUG} 또는 그 이하 레벨 설정</li>
 *   <li>APM 에이전트의 HTTP body/header 캡처 옵션</li>
 *   <li>{@link KnockClientConfig}의 {@code wiretap} 값을 {@code true}로 변경</li>
 * </ul>
 *
 * <p><b>향후 마이그레이션:</b> 규모가 커지면 Knock User API({@code PUT /v1/users/{userId}})로
 * 수신자를 사전 등록하고 워크플로우 트리거 시 {@code id}만 전달하는 방식으로 전환해
 * 본문에서 PII를 완전히 제거하는 것을 권장한다.
 *
 * <p>EMAIL/SMS 각 채널은 별도 Knock 워크플로우로 트리거된다.
 * 하나 채널 실패 시 다른 채널 트리거를 계속 시도한다.
 * 모든 채널이 실패하면 RuntimeException을 던진다.
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
    public void send(UserContact recipient, NotificationContent content, Long dispatchId,
                     Set<NotificationChannel> channels) {
        if (channels == null || channels.isEmpty()) {
            log.warn("[Knock] channels가 비어있어 발송 스킵: userId={}, dispatchId={}",
                    recipient.userId(), dispatchId);
            return;
        }

        int failCount = 0;
        KnockDispatchException lastException = null;

        for (NotificationChannel channel : channels) {
            if (!hasRequiredContact(recipient, channel)) {
                log.warn("[Knock] {} 채널 발송 스킵 — 연락처 미등록: userId={}, dispatchId={}",
                        channel, recipient.userId(), dispatchId);
                failCount++;
                continue;
            }

            String workflowKey = resolveWorkflowKey(channel);
            try {
                triggerWorkflow(workflowKey, recipient, content, dispatchId);
                log.info("[Knock] 발송 성공: userId={}, channel={}, dispatchId={}",
                        recipient.userId(), channel, dispatchId);
            } catch (KnockDispatchException ex) {
                failCount++;
                lastException = ex;
                log.warn("[Knock] 발송 실패: userId={}, channel={}, dispatchId={}, reason={}, error={}",
                        recipient.userId(), channel, dispatchId, ex.getReason(), ex.getMessage());
            }
        }

        if (failCount > 0 && failCount == channels.size()) {
            String msg = String.format("[Knock] 모든 채널 발송 실패: userId=%d, dispatchId=%d",
                    recipient.userId(), dispatchId);
            FallbackReason reason = lastException != null
                    ? lastException.getReason()
                    : FallbackReason.KNOCK_UNAVAILABLE;
            throw new KnockDispatchException(reason, msg, lastException);
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
     *
     * <p>data 페이로드 계약(이메일 Liquid 템플릿이 결정적으로 렌더링):
     * <pre>
     * data: { title, summary, services:[{name,status,area,place,target,
     *          receipt_start,receipt_end,url,image_url,
     *          changes:[{label,old,new}]}], dispatch_id }
     * </pre>
     * null 필드는 {@code @JsonInclude} 없이 직접 생략한다(빈 키 미포함).
     *
     * <p><b>멱등성(중복 발송 방어):</b> Knock REST API 표준 {@code Idempotency-Key} HTTP 헤더를
     * 실어 at-least-once 재시도 시 실발송 중복을 방지한다(Knock은 ~24h 동안 동일 키 요청을 dedup).
     * 키 = {@code dispatchId + ":" + workflowKey}:
     * <ul>
     *   <li>같은 dispatch 재시도 → dispatchId·workflowKey 동일 → 동일 키 → Knock이 멱등 처리</li>
     *   <li>EMAIL/SMS는 workflowKey가 달라 키가 분리 → 채널별로 각각 정상 발송(서로 dedup 안 됨)</li>
     * </ul>
     * 재시도 윈도우(최대 5h~12h)가 Knock 멱등 보관 기간(통상 24h) 내라 유효하다.
     */
    private void triggerWorkflow(String workflowKey, UserContact recipient,
                                 NotificationContent content, Long dispatchId) {
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
                "data", toDataPayload(content, dispatchId)
        );

        // dispatchId:workflowKey — 같은 dispatch의 같은 채널 재시도는 동일 키, 채널별은 분리.
        String idempotencyKey = dispatchId + ":" + workflowKey;

        try {
            knockWebClient.post()
                    .uri("/v1/workflows/{key}/trigger", workflowKey)
                    .header("Idempotency-Key", idempotencyKey)
                    .bodyValue(requestBody)
                    .retrieve()
                    .onStatus(status -> status.is5xxServerError(),
                            resp -> resp.createException().map(ex ->
                                    new KnockDispatchException(FallbackReason.KNOCK_SERVER_ERROR,
                                            "Knock 서버 오류: " + resp.statusCode(), ex)))
                    .bodyToMono(Void.class)
                    .timeout(Duration.ofSeconds(props.timeoutSeconds()))
                    .block();
        } catch (KnockDispatchException e) {
            throw e;
        } catch (Exception e) {
            throw new KnockDispatchException(classifyException(e),
                    "Knock 워크플로우 트리거 실패: workflowKey=" + workflowKey, e);
        }
    }

    /**
     * {@link NotificationContent}를 Knock data 페이로드 Map으로 변환한다.
     * null 필드는 키 자체를 생략한다(NON_NULL). 도메인은 JSON을 모르므로 변환은 어댑터 책임이다.
     */
    private Map<String, Object> toDataPayload(NotificationContent content, Long dispatchId) {
        Map<String, Object> data = new LinkedHashMap<>();
        putIfText(data, "title", content.title());
        putIfText(data, "summary", content.summary());
        List<Map<String, Object>> services = content.services().stream()
                .map(this::toServiceMap)
                .toList();
        data.put("services", services);
        data.put("dispatch_id", String.valueOf(dispatchId));
        return data;
    }

    private Map<String, Object> toServiceMap(NotificationContent.ServiceCard card) {
        Map<String, Object> m = new LinkedHashMap<>();
        putIfText(m, "name", card.name());
        putIfText(m, "status", card.status());
        putIfText(m, "area", card.area());
        putIfText(m, "place", card.place());
        putIfText(m, "target", card.target());
        putIfText(m, "receipt_start", card.receiptStart());
        putIfText(m, "receipt_end", card.receiptEnd());
        putIfText(m, "url", card.url());
        putIfText(m, "image_url", card.imageUrl());
        List<Map<String, Object>> changes = card.changes().stream()
                .map(this::toChangeMap)
                .toList();
        m.put("changes", changes);
        return m;
    }

    private Map<String, Object> toChangeMap(NotificationContent.ChangeLine line) {
        Map<String, Object> m = new LinkedHashMap<>();
        putIfText(m, "label", line.label());
        putIfText(m, "old", line.oldValue());
        putIfText(m, "new", line.newValue());
        return m;
    }

    private void putIfText(Map<String, Object> map, String key, String value) {
        if (StringUtils.hasText(value)) {
            map.put(key, value);
        }
    }

    /**
     * WebClient/Reactor 계층의 예외를 {@link FallbackReason}으로 분류한다.
     */
    private FallbackReason classifyException(Exception e) {
        Throwable cause = e.getCause() != null ? e.getCause() : e;
        if (cause instanceof TimeoutException
                || cause.getClass().getName().contains("TimeoutException")) {
            return FallbackReason.KNOCK_TIMEOUT;
        }
        if (e instanceof WebClientResponseException wce && wce.getStatusCode().is5xxServerError()) {
            return FallbackReason.KNOCK_SERVER_ERROR;
        }
        return FallbackReason.KNOCK_UNAVAILABLE;
    }
}
