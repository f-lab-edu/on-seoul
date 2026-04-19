package dev.jazzybyte.onseoul.collector;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.collector.config.KakaoApiProperties;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.*;
import org.springframework.web.reactive.function.client.WebClient;

import java.io.IOException;
import java.math.BigDecimal;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class KakaoGeocodingClientTest {

    static MockWebServer mockWebServer;
    KakaoGeocodingClient client;

    @BeforeAll
    static void startServer() throws IOException {
        mockWebServer = new MockWebServer();
        mockWebServer.start();
    }

    @AfterAll
    static void stopServer() throws IOException {
        mockWebServer.shutdown();
    }

    @BeforeEach
    void setUp() {
        KakaoApiProperties properties = new KakaoApiProperties();
        properties.setBaseUrl("http://localhost:" + mockWebServer.getPort());
        properties.setKey("test-api-key");

        WebClient kakaoWebClient = WebClient.builder()
                .baseUrl(properties.getBaseUrl())
                .defaultHeader("Authorization", "KakaoAK " + properties.getKey())
                .build();

        client = new KakaoGeocodingClient(kakaoWebClient, new ObjectMapper());
    }

    @Test
    @DisplayName("장소명으로 검색하면 좌표를 반환한다")
    void addressSearch_returns_coords_for_place() throws InterruptedException {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .addHeader("Content-Type", "application/json")
                .setBody("""
                        {"documents":[{"x":"126.9784","y":"37.5665","place_name":"서울시청"}],
                         "meta":{"total_count":1}}
                        """));

        Optional<BigDecimal[]> result = client.addressSearch("서울시청");

        assertThat(result).isPresent();
        assertThat(result.get()[0]).isEqualByComparingTo("126.9784");
        assertThat(result.get()[1]).isEqualByComparingTo("37.5665");

        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getPath()).contains("query=%EC%84%9C%EC%9A%B8%EC%8B%9C%EC%B2%AD"); // URL-encoded "서울시청"
        assertThat(request.getHeader("Authorization")).isEqualTo("KakaoAK test-api-key");
    }

    @Test
    @DisplayName("검색 결과가 없으면 Optional.empty()를 반환한다")
    void addressSearch_returns_empty_when_no_result() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .addHeader("Content-Type", "application/json")
                .setBody("""
                        {"documents":[],"meta":{"total_count":0}}
                        """));

        Optional<BigDecimal[]> result = client.addressSearch("존재하지않는장소명");

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("API 오류(4xx/5xx) 시 Optional.empty()를 반환한다")
    void addressSearch_returns_empty_on_api_error() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));

        Optional<BigDecimal[]> result = client.addressSearch("서울시청");

        assertThat(result).isEmpty();
    }
}
