package dev.jazzybyte.onseoul.collection.adapter.out.agent;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.web.reactive.function.client.WebClient;

import java.io.IOException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AiCacheFlushClientTest {

    private static final String SECRET_TOKEN = "secret-internal-token";

    private MockWebServer mockWebServer;
    private AiCacheFlushClient client;
    private ListAppender<ILoggingEvent> logAppender;
    private Logger clientLogger;

    @BeforeEach
    void setUp() throws IOException {
        mockWebServer = new MockWebServer();
        mockWebServer.start();

        String baseUrl = mockWebServer.url("/").toString();
        EmbeddingSyncProperties properties =
                new EmbeddingSyncProperties(baseUrl, 10, SECRET_TOKEN, 3);
        WebClient webClient = WebClient.builder().baseUrl(baseUrl).build();
        client = new AiCacheFlushClient(webClient, properties);

        clientLogger = (Logger) LoggerFactory.getLogger(AiCacheFlushClient.class);
        logAppender = new ListAppender<>();
        logAppender.start();
        clientLogger.addAppender(logAppender);
    }

    @AfterEach
    void tearDown() throws IOException {
        clientLogger.detachAppender(logAppender);
        mockWebServer.shutdown();
    }

    @Test
    @DisplayName("올바른 URL + X-Internal-Token 헤더로 flush를 호출한다")
    void flush_sendsCorrectUrlAndToken() throws InterruptedException {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"deleted\":12}"));

        client.flush();

        RecordedRequest recorded = mockWebServer.takeRequest();
        assertThat(recorded.getMethod()).isEqualTo("POST");
        assertThat(recorded.getPath()).isEqualTo("/admin/cache/flush");
        assertThat(recorded.getHeader("X-Internal-Token")).isEqualTo(SECRET_TOKEN);
    }

    @Test
    @DisplayName("요청 본문은 비어 있다 (body 없음)")
    void flush_sendsNoBody() throws InterruptedException {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"deleted\":0}"));

        client.flush();

        RecordedRequest recorded = mockWebServer.takeRequest();
        assertThat(recorded.getBodySize()).isZero();
        assertThat(recorded.getBody().readUtf8()).isEmpty();
    }

    @Test
    @DisplayName("성공 시 단 1회만 호출한다 (카테고리 무관 전역 flush)")
    void flush_success_callsExactlyOnce() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"deleted\":12}"));

        client.flush();

        assertThat(mockWebServer.getRequestCount()).isEqualTo(1);
    }

    @Test
    @DisplayName("지속 5xx 시 최초+재시도 = 정확히 2회만 호출한다 (재시도 과다 방지)")
    void flush_persistent5xx_callsExactlyTwice() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));
        // 3번째 응답을 두지 않음 — 3회 이상 호출되면 hang/추가 enqueue 필요로 드러난다.

        assertThatThrownBy(() -> client.flush()).isInstanceOf(RuntimeException.class);
        assertThat(mockWebServer.getRequestCount()).isEqualTo(2);
    }

    @Test
    @DisplayName("응답 지연이 타임아웃을 넘으면 예외를 전파한다 (best-effort 처리는 호출자)")
    void flush_timeout_throws() {
        // timeout=3s. 재시도 1회까지 모두 4s 지연 → 두 시도 모두 타임아웃.
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"deleted\":1}")
                .setHeadersDelay(4, java.util.concurrent.TimeUnit.SECONDS));
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"deleted\":1}")
                .setHeadersDelay(4, java.util.concurrent.TimeUnit.SECONDS));

        assertThatThrownBy(() -> client.flush())
                .isInstanceOf(RuntimeException.class);
    }

    @Test
    @DisplayName("로그에 시크릿 토큰을 노출하지 않고 deleted 건수만 남긴다")
    void flush_doesNotLogSecretToken_onlyDeletedCount() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"deleted\":7}"));

        client.flush();

        assertThat(logAppender.list).isNotEmpty();
        boolean tokenLeaked = logAppender.list.stream()
                .map(ILoggingEvent::getFormattedMessage)
                .anyMatch(msg -> msg.contains(SECRET_TOKEN));
        assertThat(tokenLeaked).as("로그에 X-Internal-Token 값이 노출되면 안 된다").isFalse();

        boolean deletedLogged = logAppender.list.stream()
                .map(ILoggingEvent::getFormattedMessage)
                .anyMatch(msg -> msg.contains("deleted=7"));
        assertThat(deletedLogged).as("deleted 건수는 로그에 남겨야 한다").isTrue();
    }

    @Test
    @DisplayName("401을 받으면 예외를 전파한다 (호출자가 best-effort 처리)")
    void flush_unauthorized_throws() {
        // 재시도 1회까지 모두 401이도록 2개 enqueue
        mockWebServer.enqueue(new MockResponse().setResponseCode(401));
        mockWebServer.enqueue(new MockResponse().setResponseCode(401));

        assertThatThrownBy(() -> client.flush()).isInstanceOf(RuntimeException.class);
    }

    @Test
    @DisplayName("5xx를 받으면 예외를 전파한다 (재시도 1회 후에도 실패)")
    void flush_serverError_throws() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));

        assertThatThrownBy(() -> client.flush()).isInstanceOf(RuntimeException.class);
    }

    @Test
    @DisplayName("첫 호출 5xx 후 재시도가 성공하면 정상 반환한다 (재시도 1회)")
    void flush_retriesOnce_thenSucceeds() throws InterruptedException {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"deleted\":3}"));

        client.flush();

        // 최초 + 재시도 = 2회 호출
        assertThat(mockWebServer.getRequestCount()).isEqualTo(2);
    }
}
