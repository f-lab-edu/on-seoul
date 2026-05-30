package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
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

    @Test
    @DisplayName("EMAIL 채널 → emailWorkflowKey로 Knock 트리거, recipient에 email 포함")
    void send_emailChannel_triggersEmailWorkflow() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(FULL_CONTACT, "제목", "본문", 100L, Set.of(NotificationChannel.EMAIL));

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

        adapter.send(FULL_CONTACT, "제목", "본문", 200L, Set.of(NotificationChannel.SMS));

        RecordedRequest request = mockWebServer.takeRequest();
        String body = request.getBody().readUtf8();
        assertThat(request.getPath()).contains("sms-workflow");
        assertThat(body).contains("+821012345678");           // 인라인 phone_number 식별
    }

    @Test
    @DisplayName("EMAIL+SMS 복수 채널 → 두 번 트리거")
    void send_bothChannels_triggersTwice() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(FULL_CONTACT, "제목", "본문", 300L,
                Set.of(NotificationChannel.EMAIL, NotificationChannel.SMS));

        assertThat(mockWebServer.getRequestCount()).isEqualTo(2);
    }

    @Test
    @DisplayName("모든 채널 실패(5xx) → RuntimeException throw")
    void send_allChannelsFail_throwsRuntimeException() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("error"));

        assertThatThrownBy(() ->
                adapter.send(FULL_CONTACT, "제목", "본문", 400L, Set.of(NotificationChannel.EMAIL)))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("모든 채널 발송 실패");
    }

    @Test
    @DisplayName("일부 채널 실패 시 다른 채널은 정상 발송 — 예외 없음")
    void send_oneChannelFails_otherChannelSucceeds() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("error"));
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(FULL_CONTACT, "제목", "본문", 500L,
                Set.of(NotificationChannel.EMAIL, NotificationChannel.SMS));

        assertThat(mockWebServer.getRequestCount()).isEqualTo(2);
    }

    @Test
    @DisplayName("빈 channels → HTTP 요청 없이 조용히 리턴")
    void send_emptyChannels_skipsWithoutHttpCall() {
        adapter.send(FULL_CONTACT, "제목", "본문", 600L, new HashSet<>());

        assertThat(mockWebServer.getRequestCount()).isEqualTo(0);
    }

    @Test
    @DisplayName("EMAIL 채널인데 email이 null → 연락처 없음으로 스킵, RuntimeException throw")
    void send_emailChannel_nullEmail_skipsAndThrows() {
        UserContact noEmail = new UserContact(7L, null, "+821099990000");

        assertThatThrownBy(() ->
                adapter.send(noEmail, "제목", "본문", 700L, Set.of(NotificationChannel.EMAIL)))
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
                adapter.send(noPhone, "제목", "본문", 800L, Set.of(NotificationChannel.SMS)))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("모든 채널 발송 실패");

        assertThat(mockWebServer.getRequestCount()).isEqualTo(0);
    }
}
