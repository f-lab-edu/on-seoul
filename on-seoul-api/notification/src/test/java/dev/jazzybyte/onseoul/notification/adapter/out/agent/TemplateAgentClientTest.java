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
        // serviceName=null → fallback title이 serviceId를 사용하므로 기존 assertion(contains serviceId) 유지
        return new NotificationTemplateRequest(List.of(
                new NotificationTemplateRequest.ServiceChangeGroup(
                        serviceId, null, null, null, null, null, null, null, null, null,
                        List.of(new NotificationTemplateRequest.ChangeItem("UPDATED", field, oldVal, newVal)))));
    }

    @Test
    @DisplayName("generate() - AI 응답이 유효하면 TemplateSource.AI로 반환된다")
    void generate_validAiResponse_returnsAiSource() throws InterruptedException {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"제목\",\"summary\":\"본문\"}"));

        TemplateResult result = client.generate(singleChangeRequest("SVC-001", "status", "예약가능", "마감"));

        assertThat(result.title()).isEqualTo("제목");
        assertThat(result.summary()).isEqualTo("본문");
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
        // 사실(field/old/new)은 Knock 카드가 그리므로 fallback summary는 serviceId 기반 안내만 담는다.
        assertThat(result.summary()).contains("SVC-001");
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
                .setBody("{\"title\":null,\"summary\":null}"));

        TemplateResult result = client.generate(singleChangeRequest("SVC-NULL", "status", "열림", "닫힘"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
    }

    @Test
    @DisplayName("generate() - AI가 title 빈 문자열을 반환하면 fallback을 사용한다")
    void generate_emptyTitle_returnsFallback() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"\",\"summary\":\"본문\"}"));

        TemplateResult result = client.generate(singleChangeRequest("SVC-002", "name", "구장", "체육관"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
    }

    @Test
    @DisplayName("generate() - AI가 body null을 반환하면 fallback을 사용한다")
    void generate_nullBody_returnsFallback() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"제목\",\"summary\":null}"));

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
                .setBody("{\"title\":\"제목\",\"summary\":\"본문\"}")
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
                .setBody("{\"title\":\"제목\",\"summary\":\"   \"}"));

        TemplateResult result = client.generate(singleChangeRequest("SVC-005", "location", "서울", "부산"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
    }

    @Test
    @DisplayName("generate() - 요청 JSON이 snake_case 필드 + changes 배열로 직렬화된다 (배치 모델)")
    void generate_requestBody_serializedAsSnakeCaseBatch() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"t\",\"summary\":\"b\"}"));

        NotificationTemplateRequest request = new NotificationTemplateRequest(List.of(
                new NotificationTemplateRequest.ServiceChangeGroup(
                        "SVC-001", "수영교실", "https://ex.com/1", "https://ex.com/img.png",
                        "강남센터", "강남구", "RECEIVING", "성인",
                        "2026-05-01T00:00Z", "2026-05-31T00:00Z",
                        List.of(
                                new NotificationTemplateRequest.ChangeItem("UPDATED", "service_status", "RECEIVING", "CLOSED"),
                                new NotificationTemplateRequest.ChangeItem("UPDATED", "service_name", "구", "신")))));
        client.generate(request);

        RecordedRequest recorded = mockWebServer.takeRequest();
        String body = recorded.getBody().readUtf8();

        // 최상위 그룹 래퍼 + 그룹 메타 snake_case
        assertThat(body).contains("\"services\"");
        assertThat(body).contains("\"service_id\"");
        assertThat(body).contains("\"service_name\"");
        assertThat(body).contains("\"service_url\"");
        assertThat(body).contains("\"image_url\"");
        assertThat(body).contains("\"place_name\"");
        assertThat(body).contains("\"area_name\"");
        assertThat(body).contains("\"service_status\"");
        assertThat(body).contains("\"target_info\"");
        assertThat(body).contains("\"receipt_start_dt\"");
        assertThat(body).contains("\"receipt_end_dt\"");
        // changes 배열 + 항목 snake_case
        assertThat(body).contains("\"changes\"");
        assertThat(body).contains("\"change_type\"");
        assertThat(body).contains("\"field_name\"");
        assertThat(body).contains("\"old_value\"");
        assertThat(body).contains("\"new_value\"");
        // AI 와이어는 snake_case service_id 만 쓴다. ServiceCard 내부 식별자(camelCase "serviceId")는
        // 이 경로에 들어오면 안 된다("service_id 비노출"의 camelCase 누수 방어).
        assertThat(body).doesNotContain("\"serviceId\"");
    }

    @Test
    @DisplayName("generate() - 요청 JSON에 trigger_type(CHANGE 기본)이 직렬화된다")
    void generate_requestBody_includesTriggerType() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"t\",\"summary\":\"b\"}"));

        client.generate(singleChangeRequest("SVC-001", "status", "열림", "닫힘"));

        RecordedRequest recorded = mockWebServer.takeRequest();
        String body = recorded.getBody().readUtf8();
        assertThat(body).contains("\"trigger_type\"");
        assertThat(body).contains("CHANGE");
    }

    @Test
    @DisplayName("generate() - 시점 트리거 요청은 trigger_type=DEADLINE_DDAY + changes 빈 배열로 직렬화된다")
    void generate_scheduledTrigger_serializesTriggerTypeAndEmptyChanges() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"t\",\"summary\":\"b\"}"));

        NotificationTemplateRequest request = new NotificationTemplateRequest(
                dev.jazzybyte.onseoul.notification.domain.TriggerType.DEADLINE_DDAY,
                List.of(new NotificationTemplateRequest.ServiceChangeGroup(
                        "SVC-D", "마감임박교실", null, null, null, null, "RECEIVING", null,
                        null, "2026-06-03T00:00Z", List.of())));
        client.generate(request);

        RecordedRequest recorded = mockWebServer.takeRequest();
        String body = recorded.getBody().readUtf8();
        assertThat(body).contains("\"trigger_type\"");
        assertThat(body).contains("DEADLINE_DDAY");
        assertThat(body).contains("\"changes\"");
    }

    @Test
    @DisplayName("generate() - 그룹 메타가 null이면 @JsonInclude(NON_NULL)로 직렬화에서 제외된다")
    void generate_nullMetaFields_omittedBySerialization() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"title\":\"t\",\"summary\":\"b\"}"));

        // serviceId와 changes만 채우고 나머지 메타는 모두 null
        NotificationTemplateRequest request = new NotificationTemplateRequest(List.of(
                new NotificationTemplateRequest.ServiceChangeGroup(
                        "SVC-ONLY-ID", null, null, null, null, null, null, null, null, null,
                        List.of(new NotificationTemplateRequest.ChangeItem(
                                "UPDATED", "service_status", "RECEIVING", "CLOSED")))));
        client.generate(request);

        RecordedRequest recorded = mockWebServer.takeRequest();
        String body = recorded.getBody().readUtf8();

        // 채워진 필드는 포함
        assertThat(body).contains("\"service_id\"");
        assertThat(body).contains("\"changes\"");
        // null 메타 필드는 직렬화에서 제외되어야 한다 (NON_NULL)
        assertThat(body).doesNotContain("service_name");
        assertThat(body).doesNotContain("service_url");
        assertThat(body).doesNotContain("image_url");
        assertThat(body).doesNotContain("place_name");
        assertThat(body).doesNotContain("area_name");
        assertThat(body).doesNotContain("target_info");
        assertThat(body).doesNotContain("receipt_start_dt");
        assertThat(body).doesNotContain("receipt_end_dt");
    }
}
