package dev.jazzybyte.onseoul.notification.adapter.out.agent;

import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

import java.io.IOException;
import java.util.List;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;

class TemplateAgentClientTest {

    private MockWebServer mockWebServer;
    private TemplateAgentClient client;

    @BeforeEach
    void setUp() throws IOException {
        mockWebServer = new MockWebServer();
        mockWebServer.start();

        String baseUrl = mockWebServer.url("/").toString();
        TemplateAgentProperties properties = new TemplateAgentProperties(baseUrl, 10);
        WebClient webClient = WebClient.builder().baseUrl(baseUrl).build();
        TemplateAgentDtoMapper mapper = new TemplateAgentDtoMapper();
        client = new TemplateAgentClient(webClient, mapper, properties);
    }

    @AfterEach
    void tearDown() throws IOException {
        mockWebServer.shutdown();
    }

    private NotificationTemplateRequest singleChangeRequest(String serviceId, String field,
                                                            String oldVal, String newVal) {
        return new NotificationTemplateRequest(serviceId, List.of(
                new NotificationTemplateRequest.ChangeItem("UPDATED", field, oldVal, newVal)));
    }

    @Test
    @DisplayName("generate() - AI 응답이 유효하면 TemplateSource.AI로 반환된다")
    void generate_validAiResponse_returnsAiSource() throws InterruptedException {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"제목\",\"body\":\"본문\"}"));

        TemplateResult result = client.generate(singleChangeRequest("SVC-001", "status", "예약가능", "마감"));

        assertThat(result.title()).isEqualTo("제목");
        assertThat(result.body()).isEqualTo("본문");
        assertThat(result.source()).isEqualTo(TemplateSource.AI);

        RecordedRequest recorded = mockWebServer.takeRequest();
        assertThat(recorded.getMethod()).isEqualTo("POST");
        assertThat(recorded.getPath()).isEqualTo("/notification/template");
        assertThat(recorded.getHeader("Content-Type")).contains("application/json");
    }

    @Test
    @DisplayName("generate() - AI가 500을 반환하면 fallback 템플릿을 반환한다")
    void generate_aiReturns500_returnsFallback() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));

        TemplateResult result = client.generate(singleChangeRequest("SVC-001", "status", "예약가능", "마감"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
        assertThat(result.title()).contains("SVC-001");
        assertThat(result.body()).contains("status");
    }

    @Test
    @DisplayName("generate() - AI가 4xx(400)을 반환하면 fallback 템플릿을 반환한다")
    void generate_aiReturns400_returnsFallback() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(400));

        TemplateResult result = client.generate(singleChangeRequest("SVC-400", "status", "예약가능", "마감"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
        assertThat(result.title()).contains("SVC-400");
    }

    @Test
    @DisplayName("generate() - AI가 404를 반환하면 fallback 템플릿을 반환한다")
    void generate_aiReturns404_returnsFallback() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(404));

        TemplateResult result = client.generate(singleChangeRequest("SVC-404", "status", "예약가능", "마감"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
    }

    @Test
    @DisplayName("generate() - title/body가 모두 null이면 fallback을 사용한다")
    void generate_bothNull_returnsFallback() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":null,\"body\":null}"));

        TemplateResult result = client.generate(singleChangeRequest("SVC-NULL", "status", "열림", "닫힘"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
    }

    @Test
    @DisplayName("generate() - AI가 title 빈 문자열을 반환하면 fallback을 사용한다")
    void generate_emptyTitle_returnsFallback() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"\",\"body\":\"본문\"}"));

        TemplateResult result = client.generate(singleChangeRequest("SVC-002", "name", "구장", "체육관"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
    }

    @Test
    @DisplayName("generate() - AI가 body null을 반환하면 fallback을 사용한다")
    void generate_nullBody_returnsFallback() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"제목\",\"body\":null}"));

        TemplateResult result = client.generate(singleChangeRequest("SVC-003", "date", "1월", "2월"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
    }

    @Test
    @DisplayName("generate() - 연결 거부 시 fallback 템플릿을 반환한다")
    void generate_connectionRefused_returnsFallback() throws IOException {
        mockWebServer.shutdown();

        TemplateResult result = client.generate(singleChangeRequest("SVC-004", "status", "열림", "닫힘"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
    }

    @Test
    @DisplayName("generate() - 응답이 1초 초과 지연되면 fallback을 반환한다 (timeout=1s)")
    void generate_responseDelayExceedsTimeout_returnsFallback() {
        TemplateAgentProperties fastTimeoutProperties = new TemplateAgentProperties(
                mockWebServer.url("/").toString(), 1);
        WebClient webClient = WebClient.builder()
                .baseUrl(mockWebServer.url("/").toString())
                .build();
        TemplateAgentClient fastClient = new TemplateAgentClient(
                webClient, new TemplateAgentDtoMapper(), fastTimeoutProperties);

        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"제목\",\"body\":\"본문\"}")
                .setBodyDelay(3, TimeUnit.SECONDS));

        TemplateResult result = fastClient.generate(
                singleChangeRequest("SVC-TIMEOUT", "status", "열림", "닫힘"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
    }

    @Test
    @DisplayName("generate() - AI가 body 공백 문자열을 반환하면 fallback을 사용한다")
    void generate_blankBody_returnsFallback() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"제목\",\"body\":\"   \"}"));

        TemplateResult result = client.generate(singleChangeRequest("SVC-005", "location", "서울", "부산"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
    }

    @Test
    @DisplayName("generate() - 요청 JSON이 snake_case 필드 + changes 배열로 직렬화된다 (배치 모델)")
    void generate_requestBody_serializedAsSnakeCaseBatch() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"t\",\"body\":\"b\"}"));

        NotificationTemplateRequest request = new NotificationTemplateRequest("SVC-001", List.of(
                new NotificationTemplateRequest.ChangeItem("UPDATED", "service_status", "RECEIVING", "CLOSED"),
                new NotificationTemplateRequest.ChangeItem("UPDATED", "service_name", "구", "신")
        ));
        client.generate(request);

        RecordedRequest recorded = mockWebServer.takeRequest();
        String body = recorded.getBody().readUtf8();

        assertThat(body).contains("\"service_id\"");
        assertThat(body).contains("\"changes\"");
        assertThat(body).contains("\"change_type\"");
        assertThat(body).contains("\"field_name\"");
        assertThat(body).contains("\"old_value\"");
        assertThat(body).contains("\"new_value\"");
        // 두 개의 change 항목
        assertThat(body).contains("\"service_status\"");
        assertThat(body).contains("\"service_name\"");
    }
}
