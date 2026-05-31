package dev.jazzybyte.onseoul.collection.adapter.out.agent;

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
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class EmbeddingSyncClientTest {

    private MockWebServer mockWebServer;
    private EmbeddingSyncClient client;

    @BeforeEach
    void setUp() throws IOException {
        mockWebServer = new MockWebServer();
        mockWebServer.start();

        String baseUrl = mockWebServer.url("/").toString();
        EmbeddingSyncProperties properties = new EmbeddingSyncProperties(baseUrl, 10);
        WebClient webClient = WebClient.builder().baseUrl(baseUrl).build();
        client = new EmbeddingSyncClient(webClient, properties);
    }

    @AfterEach
    void tearDown() throws IOException {
        mockWebServer.shutdown();
    }

    @Test
    @DisplayName("upsert/delete가 모두 비면 AI를 호출하지 않는다 (422 회피)")
    void emptyBoth_noCall() throws InterruptedException {
        client.sync(List.of(), List.of());

        // 호출이 없었음을 확인 — takeRequest는 즉시 null을 반환해야 한다
        RecordedRequest recorded = mockWebServer.takeRequest(500, TimeUnit.MILLISECONDS);
        assertThat(recorded).isNull();
    }

    @Test
    @DisplayName("202 응답을 정상 처리하고 요청 본문은 upsert/delete 배열로 직렬화된다")
    void sync_sendsUpsertAndDelete() throws InterruptedException {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(202)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"accepted\":{\"upsert\":2,\"delete\":1}}"));

        client.sync(List.of("SVC-1", "SVC-2"), List.of("SVC-9"));

        RecordedRequest recorded = mockWebServer.takeRequest();
        assertThat(recorded.getMethod()).isEqualTo("POST");
        assertThat(recorded.getPath()).isEqualTo("/embeddings/services/sync");
        assertThat(recorded.getHeader("Content-Type")).contains("application/json");

        String body = recorded.getBody().readUtf8();
        assertThat(body).contains("\"upsert\"");
        assertThat(body).contains("\"delete\"");
        assertThat(body).contains("SVC-1");
        assertThat(body).contains("SVC-9");
    }

    @Test
    @DisplayName("AI가 5xx를 반환하면 예외를 전파한다 (워커가 잡아 로깅)")
    void sync_serverError_throws() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));

        assertThatThrownBy(() -> client.sync(List.of("SVC-1"), List.of()))
                .isInstanceOf(RuntimeException.class);
    }

    @Test
    @DisplayName("upsert만 있어도 정상 호출된다")
    void sync_upsertOnly() throws InterruptedException {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(202)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"accepted\":{\"upsert\":1,\"delete\":0}}"));

        client.sync(List.of("SVC-1"), List.of());

        RecordedRequest recorded = mockWebServer.takeRequest();
        assertThat(recorded.getPath()).isEqualTo("/embeddings/services/sync");
    }
}
