package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

import java.io.IOException;
import java.util.HashSet;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class KnockNotificationAdapterTest {

    private MockWebServer mockWebServer;
    private KnockNotificationAdapter adapter;
    private KnockProperties props;

    // 이메일+전화번호가 모두 있는 기본 수신자
    private static final UserContact FULL_CONTACT =
            new UserContact(1L, "user@example.com", "+821012345678");

    @BeforeEach
    void setUp() throws IOException {
        mockWebServer = new MockWebServer();
        mockWebServer.start();

        props = new KnockProperties("test-api-key", "email-workflow", "sms-workflow", 10);

        WebClient webClient = WebClient.builder()
                .baseUrl(mockWebServer.url("/").toString())
                .defaultHeader("Authorization", "Bearer test-api-key")
                .defaultHeader("Content-Type", "application/json")
                .build();

        adapter = new KnockNotificationAdapter(webClient, props);
    }

    @AfterEach
    void tearDown() throws IOException {
        mockWebServer.shutdown();
    }

    private NotificationContent content() {
        return new NotificationContent("제목", "요약", java.util.List.of());
    }

    @Test
    @DisplayName("data 페이로드 계약: title/summary/services/dispatch_id 키 + 카드 필드 + 한글 라벨")
    void send_dataPayload_containsContractKeysAndKoreanLabels() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        NotificationContent content = new NotificationContent(
                "변경 알림", "1개 서비스 변경",
                java.util.List.of(new NotificationContent.ServiceCard(
                        "OA-SECRET-1", "수영교실", "접수중", "강남구", "강남센터", "성인",
                        "2026-05-01", "2026-05-31",
                        "https://ex.com/1", "https://ex.com/img.png",
                        java.util.List.of(new NotificationContent.ChangeLine("모집상태", "접수중", "예약마감")))));

        adapter.send(FULL_CONTACT, content, 900L, Set.of(NotificationChannel.EMAIL));

        RecordedRequest request = mockWebServer.takeRequest();
        String body = request.getBody().readUtf8();

        assertThat(body).contains("\"title\"").contains("변경 알림");
        assertThat(body).contains("\"summary\"").contains("1개 서비스 변경");
        assertThat(body).contains("\"services\"");
        assertThat(body).contains("\"dispatch_id\"").contains("900");
        // 카드 키 (snake_case)
        assertThat(body).contains("\"name\"").contains("수영교실");
        assertThat(body).contains("\"status\"").contains("접수중");
        assertThat(body).contains("\"area\"").contains("강남구");
        assertThat(body).contains("\"place\"").contains("강남센터");
        assertThat(body).contains("\"target\"").contains("성인");
        assertThat(body).contains("\"receipt_start\"");
        assertThat(body).contains("\"receipt_end\"");
        assertThat(body).contains("\"url\"");
        assertThat(body).contains("\"image_url\"");
        // changes[].label 한글 매핑 (camelCase field_name 미노출)
        assertThat(body).contains("\"changes\"");
        assertThat(body).contains("\"label\"").contains("모집상태");
        assertThat(body).contains("\"old\"").contains("\"new\"");
        assertThat(body).doesNotContain("serviceStatus");
        // serviceId 는 payload 내부 식별자 — Knock wire 에 노출되지 않아야 한다("service_id 비노출").
        assertThat(body).doesNotContain("serviceId");
        assertThat(body).doesNotContain("OA-SECRET-1");
    }

    @Test
    @DisplayName("data 페이로드: 카드의 null 필드는 키 자체가 생략된다 (NON_NULL)")
    void send_dataPayload_omitsNullCardFields() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        NotificationContent content = new NotificationContent(
                "제목", "요약",
                java.util.List.of(new NotificationContent.ServiceCard(
                        "OA-2", "행사", null, null, null, null, null, null, null, null,
                        java.util.List.of())));

        adapter.send(FULL_CONTACT, content, 901L, Set.of(NotificationChannel.EMAIL));

        RecordedRequest request = mockWebServer.takeRequest();
        String body = request.getBody().readUtf8();

        assertThat(body).contains("\"name\"");
        assertThat(body).doesNotContain("\"status\"");
        assertThat(body).doesNotContain("\"area\"");
        assertThat(body).doesNotContain("\"place\"");
        assertThat(body).doesNotContain("\"image_url\"");
    }

    @Test
    @DisplayName("EMAIL 채널 → emailWorkflowKey로 Knock 트리거, recipient에 email 포함")
    void send_emailChannel_triggersEmailWorkflow() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(FULL_CONTACT, content(), 100L, Set.of(NotificationChannel.EMAIL));

        RecordedRequest request = mockWebServer.takeRequest();
        String body = request.getBody().readUtf8();
        assertThat(request.getPath()).contains("email-workflow");
        assertThat(body).contains("\"1\"");                    // userId
        assertThat(body).contains("user@example.com");        // 인라인 email 식별
    }

    @Test
    @DisplayName("SMS 채널 → smsWorkflowKey로 Knock 트리거, recipient에 phone_number 포함")
    void send_smsChannel_triggersSmsWorkflow() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(FULL_CONTACT, content(), 200L, Set.of(NotificationChannel.SMS));

        RecordedRequest request = mockWebServer.takeRequest();
        String body = request.getBody().readUtf8();
        assertThat(request.getPath()).contains("sms-workflow");
        assertThat(body).contains("+821012345678");           // 인라인 phone_number 식별
    }

    @Test
    @DisplayName("Idempotency-Key 헤더 = dispatchId:workflowKey 로 실린다 (단일 채널)")
    void send_singleChannel_sendsIdempotencyKeyHeader() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(FULL_CONTACT, content(), 12345L, Set.of(NotificationChannel.EMAIL));

        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getHeader("Idempotency-Key")).isEqualTo("12345:email-workflow");
    }

    @Test
    @DisplayName("같은 dispatch의 EMAIL/SMS 두 채널은 서로 다른 Idempotency-Key를 받는다")
    void send_bothChannels_useDistinctIdempotencyKeysPerWorkflow() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(FULL_CONTACT, content(), 777L,
                Set.of(NotificationChannel.EMAIL, NotificationChannel.SMS));

        RecordedRequest first = mockWebServer.takeRequest();
        RecordedRequest second = mockWebServer.takeRequest();
        Set<String> keys = new HashSet<>();
        keys.add(first.getHeader("Idempotency-Key"));
        keys.add(second.getHeader("Idempotency-Key"));
        assertThat(keys).containsExactlyInAnyOrder("777:email-workflow", "777:sms-workflow");
    }

    @Test
    @DisplayName("동일 dispatch·동일 채널 재호출(재시도) → 동일 Idempotency-Key")
    void send_sameDispatchSameChannelRetried_usesSameIdempotencyKey() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(FULL_CONTACT, content(), 555L, Set.of(NotificationChannel.EMAIL));
        adapter.send(FULL_CONTACT, content(), 555L, Set.of(NotificationChannel.EMAIL));

        RecordedRequest first = mockWebServer.takeRequest();
        RecordedRequest second = mockWebServer.takeRequest();
        assertThat(first.getHeader("Idempotency-Key")).isEqualTo("555:email-workflow");
        assertThat(second.getHeader("Idempotency-Key")).isEqualTo("555:email-workflow");
    }

    @Test
    @DisplayName("EMAIL+SMS 복수 채널 → 두 번 트리거")
    void send_bothChannels_triggersTwice() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(FULL_CONTACT, content(), 300L,
                Set.of(NotificationChannel.EMAIL, NotificationChannel.SMS));

        assertThat(mockWebServer.getRequestCount()).isEqualTo(2);
    }

    @Test
    @DisplayName("모든 채널 실패(5xx) → RuntimeException throw")
    void send_allChannelsFail_throwsRuntimeException() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("error"));

        assertThatThrownBy(() ->
                adapter.send(FULL_CONTACT, content(), 400L, Set.of(NotificationChannel.EMAIL)))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("모든 채널 발송 실패");
    }

    @Test
    @DisplayName("일부 채널 실패 시 다른 채널은 정상 발송 — 예외 없음")
    void send_oneChannelFails_otherChannelSucceeds() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("error"));
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(FULL_CONTACT, content(), 500L,
                Set.of(NotificationChannel.EMAIL, NotificationChannel.SMS));

        assertThat(mockWebServer.getRequestCount()).isEqualTo(2);
    }

    @Test
    @DisplayName("빈 channels → HTTP 요청 없이 조용히 리턴")
    void send_emptyChannels_skipsWithoutHttpCall() {
        adapter.send(FULL_CONTACT, content(), 600L, new HashSet<>());

        assertThat(mockWebServer.getRequestCount()).isEqualTo(0);
    }

    @Test
    @DisplayName("EMAIL 채널인데 email이 null → 연락처 없음으로 스킵, RuntimeException throw")
    void send_emailChannel_nullEmail_skipsAndThrows() {
        UserContact noEmail = new UserContact(7L, null, "+821099990000");

        assertThatThrownBy(() ->
                adapter.send(noEmail, content(), 700L, Set.of(NotificationChannel.EMAIL)))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("모든 채널 발송 실패");

        // 연락처 없음 → HTTP 요청 자체를 안 보냄
        assertThat(mockWebServer.getRequestCount()).isEqualTo(0);
    }

    @Test
    @DisplayName("SMS 채널인데 phoneNumber가 null → 연락처 없음으로 스킵, RuntimeException throw")
    void send_smsChannel_nullPhone_skipsAndThrows() {
        UserContact noPhone = new UserContact(8L, "user@example.com", null);

        assertThatThrownBy(() ->
                adapter.send(noPhone, content(), 800L, Set.of(NotificationChannel.SMS)))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("모든 채널 발송 실패");

        assertThat(mockWebServer.getRequestCount()).isEqualTo(0);
    }
}
