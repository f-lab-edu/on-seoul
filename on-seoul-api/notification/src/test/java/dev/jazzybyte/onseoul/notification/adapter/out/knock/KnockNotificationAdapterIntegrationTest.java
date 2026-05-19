package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.ClientRequest;
import org.springframework.web.reactive.function.client.ExchangeFilterFunction;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

import java.util.Set;

/**
 * Knock 실계정 연동 테스트.
 *
 * <p>실제 Knock API를 호출하므로 CI에서는 실행하지 않는다.
 * 로컬에서 수동 실행 시 아래 상수를 실제 값으로 채워서 실행한다.</p>
 *
 * <pre>
 *   KNOCK_API_KEY          — Knock 대시보드 > API Keys
 *   KNOCK_EMAIL_WORKFLOW   — Knock 대시보드 > Workflows 에서 생성한 이메일 워크플로우 키
 *   KNOCK_SMS_WORKFLOW     — Knock 대시보드 > Workflows 에서 생성한 SMS 워크플로우 키
 *   KNOCK_TEST_USER_ID     — Knock 사용자 식별자 (Knock에 등록된 수신자 ID)
 * </pre>
 */
@Disabled("실계정 연동 테스트 — 로컬 수동 실행 전용. 상수 값을 채운 뒤 @Disabled 제거하고 실행한다.")
class KnockNotificationAdapterIntegrationTest {

    // ── 실행 전 아래 값을 실제 Knock 계정 정보로 교체 ──────────────────────────
    private static final String KNOCK_API_KEY        = "sk_test_REPLACE_ME";
    private static final String KNOCK_EMAIL_WORKFLOW = "service-change-email";
    private static final String KNOCK_SMS_WORKFLOW   = "service-change-sms";
    private static final long   KNOCK_TEST_USER_ID   = 1L;   // Knock에 등록된 수신자 ID
    // ──────────────────────────────────────────────────────────────────────────

    private KnockNotificationAdapter adapter;

    @BeforeEach
    void setUp() {
        KnockProperties props = new KnockProperties(
                KNOCK_API_KEY, KNOCK_EMAIL_WORKFLOW, KNOCK_SMS_WORKFLOW, 10);

        WebClient webClient = WebClient.builder()
                .baseUrl("https://api.knock.app")
                .defaultHeader("Content-Type", "application/json")
                .filter(ExchangeFilterFunction.ofRequestProcessor(req ->
                        Mono.just(ClientRequest.from(req)
                                .header("Authorization", "Bearer " + KNOCK_API_KEY)
                                .build())))
                .build();

        adapter = new KnockNotificationAdapter(webClient, props);
    }

    @Test
    @DisplayName("[실연동] EMAIL 채널 — Knock 이메일 워크플로우 트리거")
    void realKnock_emailChannel_triggersSuccessfully() {
        // 예외 없이 완료되면 성공. Knock 대시보드 > Logs 에서 수신 확인.
        adapter.send(
                KNOCK_TEST_USER_ID,
                "[on-seoul] 서비스 변경 알림 (이메일 연동 테스트)",
                "OA-2266 체육시설 예약 서비스의 상태가 변경되었습니다.",
                99901L,
                Set.of(NotificationChannel.EMAIL)
        );
    }

    @Test
    @DisplayName("[실연동] SMS 채널 — Knock SMS 워크플로우 트리거")
    void realKnock_smsChannel_triggersSuccessfully() {
        // 예외 없이 완료되면 성공. Knock 대시보드 > Logs 에서 수신 확인.
        adapter.send(
                KNOCK_TEST_USER_ID,
                "[on-seoul] 서비스 변경 알림 (SMS 연동 테스트)",
                "OA-2269 문화행사 예약 서비스의 상태가 변경되었습니다.",
                99902L,
                Set.of(NotificationChannel.SMS)
        );
    }
}
