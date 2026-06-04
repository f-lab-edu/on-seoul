package dev.jazzybyte.onseoul.chat.adapter.out.agent;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.chat.port.out.AiStreamEvent;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
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

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ChatAgentClientTest {

    private MockWebServer mockWebServer;
    private ChatAgentClient adapter;

    @BeforeEach
    void setUp() throws IOException {
        mockWebServer = new MockWebServer();
        mockWebServer.start();

        String baseUrl = mockWebServer.url("/").toString();
        AiServiceProperties properties = new AiServiceProperties(baseUrl, 30);
        WebClient webClient = WebClient.builder().baseUrl(baseUrl).build();
        adapter = new ChatAgentClient(webClient, properties);
    }

    @AfterEach
    void tearDown() throws IOException {
        mockWebServer.shutdown();
    }

    @Test
    @DisplayName("stream() - AI 서비스가 SSE 토큰을 정상 반환하면 Flux<String>으로 수신한다")
    void stream_happyPath_returnsTokens() {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: 안녕\n\ndata: 하세요\n\n")
                .setResponseCode(200));

        List<AiStreamEvent> events = adapter.stream("서울 문화행사 알려줘", 1L, 10L, null, null, java.util.List.of())
                .collectList()
                .block();

        assertThat(events).extracting(AiStreamEvent::raw).containsExactly("안녕", "하세요");
        assertThat(events).noneMatch(AiStreamEvent::isFinal);
    }

    @Test
    @DisplayName("stream() - AI 서비스가 500을 반환하면 OnSeoulApiException(AI_SERVICE_ERROR)으로 매핑된다")
    void stream_aiServiceReturns500_wrapsInOnSeoulApiException() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(500)
                .setBody("{\"error\": \"Internal Server Error\"}"));

        assertThatThrownBy(() ->
                adapter.stream("질문", 1L, 10L, null, null, java.util.List.of()).collectList().block()
        )
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.AI_SERVICE_ERROR));
    }

    @Test
    @DisplayName("stream() - 연결 거부 시 OnSeoulApiException(AI_SERVICE_ERROR)으로 매핑된다")
    void stream_connectionRefused_wrapsInOnSeoulApiException() throws IOException {
        mockWebServer.shutdown();

        assertThatThrownBy(() ->
                adapter.stream("질문", 1L, 10L, null, null, java.util.List.of()).collectList().block()
        )
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.AI_SERVICE_ERROR));
    }

    @Test
    @DisplayName("stream() - data 필드가 없는 SSE 이벤트(keep-alive)는 건너뛰고 유효한 토큰만 반환한다")
    void stream_emptyDataField_filteredOut() {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody(": keep-alive\n\ndata: 토큰\n\n")
                .setResponseCode(200));

        List<AiStreamEvent> events = adapter.stream("질문", 1L, 10L, null, null, java.util.List.of())
                .collectList()
                .block();

        assertThat(events).extracting(AiStreamEvent::raw).containsExactly("토큰");
    }

    @Test
    @DisplayName("stream() - lat/lng가 null이면 직렬화된 JSON 요청 본문에 lat/lng 필드가 포함되지 않는다 (@JsonInclude(NON_NULL) 검증)")
    void stream_nullLatLng_excludedFromRequestBody() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: ok\n\n")
                .setResponseCode(200));

        adapter.stream("서울 문화행사", 1L, 10L, null, null, java.util.List.of()).collectList().block();

        RecordedRequest recorded = mockWebServer.takeRequest();
        String body = recorded.getBody().readUtf8();
        JsonNode json = new ObjectMapper().readTree(body);

        assertThat(json.has("lat")).isFalse();
        assertThat(json.has("lng")).isFalse();
        assertThat(json.get("room_id").asLong()).isEqualTo(1L);
        assertThat(json.get("message_id").asLong()).isEqualTo(10L);
        assertThat(json.get("message").asText()).isEqualTo("서울 문화행사");
    }

    @Test
    @DisplayName("stream() - lat/lng가 존재하면 직렬화된 JSON 요청 본문에 lat/lng 필드가 포함된다")
    void stream_withLatLng_includedInRequestBody() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: ok\n\n")
                .setResponseCode(200));

        adapter.stream("근처 체육시설", 2L, 20L, 37.5665, 126.9780, java.util.List.of()).collectList().block();

        RecordedRequest recorded = mockWebServer.takeRequest();
        String body = recorded.getBody().readUtf8();
        JsonNode json = new ObjectMapper().readTree(body);

        assertThat(json.has("lat")).isTrue();
        assertThat(json.has("lng")).isTrue();
        assertThat(json.get("lat").asDouble()).isEqualTo(37.5665);
        assertThat(json.get("lng").asDouble()).isEqualTo(126.9780);
        assertThat(json.get("room_id").asLong()).isEqualTo(2L);
        assertThat(json.get("message_id").asLong()).isEqualTo(20L);
        assertThat(json.get("message").asText()).isEqualTo("근처 체육시설");
    }

    @Test
    @DisplayName("stream() - history가 \"history\" 배열로 직렬화되고 각 항목이 {role,content} 소문자 role로 전송된다")
    void stream_history_serializedAsArray() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: ok\n\n")
                .setResponseCode(200));

        List<dev.jazzybyte.onseoul.chat.domain.ChatTurn> history = List.of(
                new dev.jazzybyte.onseoul.chat.domain.ChatTurn("user", "강남구 문화행사 알려줘"),
                new dev.jazzybyte.onseoul.chat.domain.ChatTurn("assistant", "강남구 문화행사 5건을 안내합니다."));

        adapter.stream("그 중 무료인 것만", 5L, 7L, null, null, history).collectList().block();

        RecordedRequest recorded = mockWebServer.takeRequest();
        JsonNode json = new ObjectMapper().readTree(recorded.getBody().readUtf8());

        assertThat(json.get("history").isArray()).isTrue();
        assertThat(json.get("history")).hasSize(2);
        assertThat(json.get("history").get(0).get("role").asText()).isEqualTo("user");
        assertThat(json.get("history").get(0).get("content").asText()).isEqualTo("강남구 문화행사 알려줘");
        assertThat(json.get("history").get(1).get("role").asText()).isEqualTo("assistant");
        assertThat(json.get("history").get(1).get("content").asText()).isEqualTo("강남구 문화행사 5건을 안내합니다.");
    }

    @Test
    @DisplayName("stream() - history가 비어 있으면 \"history\"는 빈 배열로 직렬화된다")
    void stream_emptyHistory_serializedAsEmptyArray() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: ok\n\n")
                .setResponseCode(200));

        adapter.stream("질문", 1L, 10L, null, null, java.util.List.of()).collectList().block();

        RecordedRequest recorded = mockWebServer.takeRequest();
        JsonNode json = new ObjectMapper().readTree(recorded.getBody().readUtf8());

        assertThat(json.get("history").isArray()).isTrue();
        assertThat(json.get("history")).isEmpty();
    }

    @Test
    @DisplayName("stream() - answer 키가 있고 error 키가 없는 data는 final 이벤트로 인식되고 answer가 추출된다")
    void stream_finalEvent_extractsAnswer() {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: {\"stage\":\"routing\"}\n\n"
                        + "data: {\"message_id\":84,\"answer\":\"강남구 문화행사 안내\",\"intent\":\"SQL_SEARCH\"}\n\n")
                .setResponseCode(200));

        List<AiStreamEvent> events = adapter.stream("질문", 1L, 10L, null, null, java.util.List.of())
                .collectList()
                .block();

        assertThat(events).hasSize(2);
        assertThat(events.get(0).isFinal()).isFalse();
        assertThat(events.get(1).isFinal()).isTrue();
        assertThat(events.get(1).finalAnswer()).isEqualTo("강남구 문화행사 안내");
        // 원본 data는 양쪽 모두 그대로 보존된다(프론트 relay용)
        assertThat(events.get(1).raw()).contains("\"message_id\":84");
    }

    @Test
    @DisplayName("stream() - error 키가 함께 있는 data(workflow_error)는 final로 저장되지 않는다(relay 전용)")
    void stream_workflowError_notTreatedAsFinal() {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: {\"answer\":\"폴백 답변\",\"error\":\"처리 중 오류\"}\n\n")
                .setResponseCode(200));

        List<AiStreamEvent> events = adapter.stream("질문", 1L, 10L, null, null, java.util.List.of())
                .collectList()
                .block();

        assertThat(events).hasSize(1);
        assertThat(events.get(0).isFinal()).isFalse();
        assertThat(events.get(0).raw()).contains("폴백 답변");
    }

    @Test
    @DisplayName("stream() - answer가 null인 final data는 빈 문자열로 추출된다")
    void stream_finalWithNullAnswer_extractsEmptyString() {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: {\"message_id\":1,\"answer\":null,\"intent\":\"MAP\"}\n\n")
                .setResponseCode(200));

        List<AiStreamEvent> events = adapter.stream("질문", 1L, 10L, null, null, java.util.List.of())
                .collectList()
                .block();

        assertThat(events).hasSize(1);
        assertThat(events.get(0).isFinal()).isTrue();
        assertThat(events.get(0).finalAnswer()).isEmpty();
    }

    @Test
    @DisplayName("stream() - final 이벤트에 service_cards 배열이 있으면 그 배열이 compact JSON으로 직렬화된다")
    void stream_finalWithServiceCards_serializesArray() throws Exception {
        String finalData = "{\"message_id\":84,\"answer\":\"강남구 문화행사 안내\","
                + "\"service_cards\":["
                + "{\"service_id\":\"S1\",\"name\":\"강남 음악회 🎵\",\"area\":\"강남구\"},"
                + "{\"service_id\":\"S2\",\"name\":\"미술 전시\",\"area\":\"강남구\"}"
                + "]}";
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: " + finalData + "\n\n")
                .setResponseCode(200));

        List<AiStreamEvent> events = adapter.stream("강남구 문화행사", 1L, 84L, null, null, java.util.List.of())
                .collectList()
                .block();

        assertThat(events).hasSize(1);
        AiStreamEvent fin = events.get(0);
        assertThat(fin.isFinal()).isTrue();
        assertThat(fin.finalAnswer()).isEqualTo("강남구 문화행사 안내");

        // service_cards는 배열 그대로 직렬화되어야 한다(앞뒤가 [ ] 이고, 문자열로 escape되지 않음).
        String cards = fin.finalServiceCards();
        assertThat(cards).isNotNull();
        assertThat(cards).startsWith("[").endsWith("]");
        // compact: writeValueAsString 결과는 다시 파싱 가능한 배열이어야 한다.
        JsonNode parsed = new ObjectMapper().readTree(cards);
        assertThat(parsed.isArray()).isTrue();
        assertThat(parsed).hasSize(2);
        assertThat(parsed.get(0).get("service_id").asText()).isEqualTo("S1");
        // 한글/이모지 보존
        assertThat(parsed.get(0).get("name").asText()).isEqualTo("강남 음악회 🎵");
    }

    @Test
    @DisplayName("stream() - final 이벤트에 service_cards 키가 없으면 finalServiceCards는 null")
    void stream_finalWithoutServiceCardsKey_nullCards() {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: {\"message_id\":1,\"answer\":\"답변\",\"intent\":\"SQL_SEARCH\"}\n\n")
                .setResponseCode(200));

        AiStreamEvent fin = adapter.stream("질문", 1L, 1L, null, null, java.util.List.of())
                .blockLast();

        assertThat(fin.isFinal()).isTrue();
        assertThat(fin.finalServiceCards()).isNull();
    }

    @Test
    @DisplayName("stream() - final 이벤트의 service_cards가 명시적 null이면 finalServiceCards는 null")
    void stream_finalWithNullServiceCards_nullCards() {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: {\"message_id\":1,\"answer\":\"답변\",\"service_cards\":null}\n\n")
                .setResponseCode(200));

        AiStreamEvent fin = adapter.stream("질문", 1L, 1L, null, null, java.util.List.of())
                .blockLast();

        assertThat(fin.isFinal()).isTrue();
        assertThat(fin.finalServiceCards()).isNull();
    }

    @Test
    @DisplayName("stream() - final 이벤트의 service_cards가 빈 배열이면 finalServiceCards는 \"[]\" (명시적 빈 배열은 보존)")
    void stream_finalWithEmptyServiceCards_preservesEmptyArray() {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: {\"message_id\":1,\"answer\":\"답변\",\"service_cards\":[]}\n\n")
                .setResponseCode(200));

        AiStreamEvent fin = adapter.stream("질문", 1L, 1L, null, null, java.util.List.of())
                .blockLast();

        assertThat(fin.isFinal()).isTrue();
        assertThat(fin.finalServiceCards()).isEqualTo("[]");
    }

    @Test
    @DisplayName("stream() - answer와 error가 함께 있으면 final이 아니므로 service_cards도 캡처되지 않는다")
    void stream_answerWithError_notFinal_noCards() {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: {\"answer\":\"폴백 답변\",\"error\":\"오류\","
                        + "\"service_cards\":[{\"service_id\":\"S1\"}]}\n\n")
                .setResponseCode(200));

        AiStreamEvent ev = adapter.stream("질문", 1L, 1L, null, null, java.util.List.of())
                .blockLast();

        assertThat(ev.isFinal()).isFalse();
        // final이 아니면 finalServiceCards는 null (relay 전용)
        assertThat(ev.finalServiceCards()).isNull();
    }

    @Test
    @DisplayName("stream() - 요청이 /chat/stream 경로로 POST 전송된다")
    void stream_requestSentToCorrectPath() throws Exception {
        mockWebServer.enqueue(new MockResponse()
                .setHeader("Content-Type", "text/event-stream")
                .setBody("data: ok\n\n")
                .setResponseCode(200));

        adapter.stream("질문", 1L, 10L, null, null, java.util.List.of()).collectList().block();

        RecordedRequest recorded = mockWebServer.takeRequest();
        assertThat(recorded.getMethod()).isEqualTo("POST");
        assertThat(recorded.getPath()).isEqualTo("/chat/stream");
        assertThat(recorded.getHeader("Content-Type")).contains("application/json");
        assertThat(recorded.getHeader("Accept")).contains("text/event-stream");
    }
}
