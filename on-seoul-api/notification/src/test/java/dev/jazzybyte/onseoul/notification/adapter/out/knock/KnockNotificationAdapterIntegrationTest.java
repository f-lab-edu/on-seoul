package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.ClientRequest;
import org.springframework.web.reactive.function.client.ExchangeFilterFunction;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

import java.util.Set;

import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * Knock 실계정 연동 테스트.
 *
 * <p>실제 Knock API를 호출한다. 아래 환경변수가 모두 설정된 경우에만 실행되며,
 * 미설정 시 자동으로 스킵된다(CI에서는 env가 없으므로 항상 스킵).</p>
 *
 * <pre>
 *   KNOCK_API_KEY              — Knock 대시보드 > Developers > API Keys (Secret key)
 *   KNOCK_EMAIL_WORKFLOW_KEY   — 이메일 워크플로우 키 (기본값: service-change-email)
 *   KNOCK_SMS_WORKFLOW_KEY     — SMS 워크플로우 키    (기본값: service-change-sms)
 *   KNOCK_TEST_USER_ID         — Knock 수신자 ID (on-seoul userId)
 *   KNOCK_TEST_EMAIL           — 테스트 수신 이메일 주소
 *   KNOCK_TEST_PHONE           — 테스트 수신 전화번호 (E.164 형식, 예: +821012345678)
 * </pre>
 *
 * 실행 예시:
 * <pre>
 *   KNOCK_API_KEY=sk_test_xxx \
 *   KNOCK_TEST_USER_ID=1 \
 *   KNOCK_TEST_EMAIL=your@email.com \
 *   KNOCK_TEST_PHONE=+821012345678 \
 *   ./gradlew :notification:test --tests "*.KnockNotificationAdapterIntegrationTest"
 * </pre>
 *
 * 결과 확인: Knock 대시보드 → Logs
 */
class KnockNotificationAdapterIntegrationTest {

    private static final String ENV_API_KEY        = "KNOCK_API_KEY";
    private static final String ENV_EMAIL_WORKFLOW = "KNOCK_EMAIL_WORKFLOW_KEY";
    private static final String ENV_SMS_WORKFLOW   = "KNOCK_SMS_WORKFLOW_KEY";
    private static final String ENV_USER_ID        = "KNOCK_TEST_USER_ID";
    private static final String ENV_TEST_EMAIL     = "KNOCK_TEST_EMAIL";
    private static final String ENV_TEST_PHONE     = "KNOCK_TEST_PHONE";

    private KnockNotificationAdapter adapter;
    private long testUserId;

    @BeforeEach
    void setUp() {
        String apiKey = System.getenv(ENV_API_KEY);
        assumeTrue(apiKey != null && !apiKey.isBlank(),
                ENV_API_KEY + " 환경변수 미설정 — Knock 연동 테스트 스킵");

        String userIdStr = System.getenv(ENV_USER_ID);
        assumeTrue(userIdStr != null && !userIdStr.isBlank(),
                ENV_USER_ID + " 환경변수 미설정 — Knock 연동 테스트 스킵");

        testUserId = Long.parseLong(userIdStr);

        String emailWorkflow = envOrDefault(ENV_EMAIL_WORKFLOW, "service-change-email");
        String smsWorkflow   = envOrDefault(ENV_SMS_WORKFLOW,   "service-change-sms");

        KnockProperties props = new KnockProperties(apiKey, emailWorkflow, smsWorkflow, 10);

        WebClient webClient = WebClient.builder()
                .baseUrl("https://api.knock.app")
                .defaultHeader("Content-Type", "application/json")
                .filter(ExchangeFilterFunction.ofRequestProcessor(req ->
                        Mono.just(ClientRequest.from(req)
                                .header("Authorization", "Bearer " + apiKey)
                                .build())))
                .build();

        adapter = new KnockNotificationAdapter(webClient, props);
    }

    @Test
    @DisplayName("[실연동] EMAIL 채널 — Knock 이메일 워크플로우 트리거")
    void realKnock_emailChannel_triggersSuccessfully() {
        String email = System.getenv(ENV_TEST_EMAIL);
        assumeTrue(email != null && !email.isBlank(),
                ENV_TEST_EMAIL + " 환경변수 미설정 — 이메일 연동 테스트 스킵");

        UserContact recipient = new UserContact(testUserId, email, null);

        // 예외 없이 완료되면 성공. Knock 대시보드 > Logs 에서 수신 확인.
        adapter.send(
                recipient,
                "[on-seoul] 서비스 변경 알림 (이메일 연동 테스트)",
                "OA-2266 체육시설 예약 서비스의 상태가 변경되었습니다.",
                99901L,
                Set.of(NotificationChannel.EMAIL)
        );
    }

    @Test
    @DisplayName("[실연동] SMS 채널 — Knock SMS 워크플로우 트리거")
    void realKnock_smsChannel_triggersSuccessfully() {
        String phone = System.getenv(ENV_TEST_PHONE);
        assumeTrue(phone != null && !phone.isBlank(),
                ENV_TEST_PHONE + " 환경변수 미설정 — SMS 연동 테스트 스킵");

        UserContact recipient = new UserContact(testUserId, null, phone);

        // 예외 없이 완료되면 성공. Knock 대시보드 > Logs 에서 수신 확인.
        adapter.send(
                recipient,
                "[on-seoul] 서비스 변경 알림 (SMS 연동 테스트)",
                "OA-2269 문화행사 예약 서비스의 상태가 변경되었습니다.",
                99902L,
                Set.of(NotificationChannel.SMS)
        );
    }

    private static String envOrDefault(String key, String defaultValue) {
        String value = System.getenv(key);
        return (value != null && !value.isBlank()) ? value : defaultValue;
    }
}
