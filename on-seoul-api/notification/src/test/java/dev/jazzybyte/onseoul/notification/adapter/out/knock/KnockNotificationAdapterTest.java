package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

import java.io.IOException;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class KnockNotificationAdapterTest {

    private MockWebServer mockWebServer;
    private KnockNotificationAdapter adapter;
    private KnockProperties props;

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
    @DisplayName("EMAIL 채널 → emailWorkflowKey로 Knock 트리거")
    void send_emailChannel_triggersEmailWorkflow() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(1L, "제목", "본문", 100L, Set.of(NotificationChannel.EMAIL));

        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getPath()).contains("email-workflow");
        assertThat(request.getBody().readUtf8()).contains("\"1\"");
    }

    @Test
    @DisplayName("SMS 채널 → smsWorkflowKey로 Knock 트리거")
    void send_smsChannel_triggersSmsWorkflow() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(2L, "제목", "본문", 200L, Set.of(NotificationChannel.SMS));

        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getPath()).contains("sms-workflow");
    }

    @Test
    @DisplayName("EMAIL+SMS 복수 채널 → 두 번 트리거")
    void send_bothChannels_triggersTwice() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        adapter.send(3L, "제목", "본문", 300L,
                Set.of(NotificationChannel.EMAIL, NotificationChannel.SMS));

        assertThat(mockWebServer.getRequestCount()).isEqualTo(2);
    }

    @Test
    @DisplayName("모든 채널 실패 → RuntimeException throw")
    void send_allChannelsFail_throwsRuntimeException() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("error"));

        assertThatThrownBy(() ->
                adapter.send(4L, "제목", "본문", 400L, Set.of(NotificationChannel.EMAIL)))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("모든 채널 발송 실패");
    }

    @Test
    @DisplayName("일부 채널 실패 시 다른 채널은 정상 발송")
    void send_oneChannelFails_otherChannelSucceeds() throws Exception {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500).setBody("error"));
        mockWebServer.enqueue(new MockResponse().setResponseCode(200).setBody("{}"));

        // EMAIL 실패 + SMS 성공 — 예외 없이 완료되어야 한다
        adapter.send(5L, "제목", "본문", 500L,
                Set.of(NotificationChannel.EMAIL, NotificationChannel.SMS));

        assertThat(mockWebServer.getRequestCount()).isEqualTo(2);
    }

    @Test
    @DisplayName("빈 channels Set → HTTP 요청 없이 조용히 리턴")
    void send_emptyChannels_skipsWithoutHttpCall() {
        // guard: channels가 비어있으면 발송 스킵 — 예외 없이 반환
        adapter.send(6L, "제목", "본문", 600L, new java.util.HashSet<>());

        assertThat(mockWebServer.getRequestCount()).isEqualTo(0);
    }
}
