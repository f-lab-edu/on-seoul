package dev.jazzybyte.onseoul.adapter.out.kakao;

import com.fasterxml.jackson.databind.ObjectMapper;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

import java.io.IOException;
import java.math.BigDecimal;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class KakaoGeocodingAdapterTest {

    private MockWebServer mockWebServer;
    private KakaoGeocodingAdapter adapter;
    private KakaoApiProperties properties;

    @BeforeEach
    void setUp() throws IOException {
        mockWebServer = new MockWebServer();
        mockWebServer.start();

        properties = new KakaoApiProperties();
        properties.setKey("test-kakao-key");
        properties.setBaseUrl(mockWebServer.url("/").toString());

        WebClient webClient = WebClient.builder()
                .baseUrl(mockWebServer.url("/").toString())
                .defaultHeader("Authorization", "KakaoAK test-kakao-key")
                .build();

        adapter = new KakaoGeocodingAdapter(webClient, new ObjectMapper(), properties);
    }

    @AfterEach
    void tearDown() throws IOException {
        mockWebServer.shutdown();
    }

    // -----------------------------------------------------------------------
    // 헬퍼: 카카오 키워드 검색 응답 JSON 생성
    // -----------------------------------------------------------------------

    private String buildSuccessResponse(String x, String y, String placeName) {
        return """
                {
                    "documents": [
                        {
                            "place_name": "%s",
                            "address_name": "서울 중구 태평로1가 31",
                            "x": "%s",
                            "y": "%s"
                        }
                    ],
                    "meta": {
                        "total_count": 1,
                        "is_end": true
                    }
                }
                """.formatted(placeName, x, y);
    }

    private String buildEmptyDocumentsResponse() {
        return """
                {
                    "documents": [],
                    "meta": {
                        "total_count": 0,
                        "is_end": true
                    }
                }
                """;
    }

    // -----------------------------------------------------------------------
    // T7-1: 정상 응답(documents[0].x, y) → Optional.of(BigDecimal[]{x, y})
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("카카오 API가 정상 응답을 반환하면 Optional.of(BigDecimal[]{x, y})를 반환한다")
    void geocode_validResponse_returnsCoordinates() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(buildSuccessResponse("126.9779", "37.5665", "서울시청")));

        Optional<BigDecimal[]> result = adapter.geocode("서울시청");

        assertThat(result).isPresent();
        BigDecimal[] coords = result.get();
        assertThat(coords).hasSize(2);
        assertThat(coords[0]).isEqualByComparingTo(new BigDecimal("126.9779"));
        assertThat(coords[1]).isEqualByComparingTo(new BigDecimal("37.5665"));
    }

    // -----------------------------------------------------------------------
    // T7-2: documents 빈 배열 → Optional.empty()
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("documents가 빈 배열이면 Optional.empty()를 반환한다")
    void geocode_emptyDocuments_returnsEmpty() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(buildEmptyDocumentsResponse()));

        Optional<BigDecimal[]> result = adapter.geocode("존재하지않는장소XYZ");

        assertThat(result).isEmpty();
    }

    // -----------------------------------------------------------------------
    // T7-3: HTTP 4xx → Optional.empty() (예외 전파 없음)
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("카카오 API가 4xx를 반환하면 Optional.empty()를 반환하고 예외를 전파하지 않는다")
    void geocode_http4xx_returnsEmptyWithoutException() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(401)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"errorType\": \"AuthorizationFailed\", \"message\": \"인증 실패\"}"));

        Optional<BigDecimal[]> result = adapter.geocode("서울시청");

        assertThat(result).isEmpty();
    }

    // -----------------------------------------------------------------------
    // T7-4: HTTP 5xx → Optional.empty() (예외 전파 없음)
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("카카오 API가 5xx를 반환하면 Optional.empty()를 반환하고 예외를 전파하지 않는다")
    void geocode_http5xx_returnsEmptyWithoutException() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(500)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"errorType\": \"InternalServerError\"}"));

        Optional<BigDecimal[]> result = adapter.geocode("서울시청");

        assertThat(result).isEmpty();
    }

    // -----------------------------------------------------------------------
    // T7-5: API 키 미설정(blank) → geocoding 호출 없이 Optional.empty()
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("API 키가 blank이면 HTTP 호출 없이 Optional.empty()를 반환한다")
    void geocode_blankApiKey_returnsEmptyWithoutHttpCall() throws IOException {
        KakaoApiProperties blankKeyProperties = new KakaoApiProperties();
        blankKeyProperties.setKey("");
        blankKeyProperties.setBaseUrl(mockWebServer.url("/").toString());

        WebClient webClient = WebClient.builder()
                .baseUrl(mockWebServer.url("/").toString())
                .build();

        KakaoGeocodingAdapter adapterWithBlankKey = new KakaoGeocodingAdapter(
                webClient, new ObjectMapper(), blankKeyProperties);

        Optional<BigDecimal[]> result = adapterWithBlankKey.geocode("서울시청");

        assertThat(result).isEmpty();
        // HTTP 요청이 발생하지 않아야 한다
        assertThat(mockWebServer.getRequestCount()).isZero();
    }

    // -----------------------------------------------------------------------
    // T7-6: 연결 거부(서버 종료) → Optional.empty() (예외 전파 없음)
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("서버 연결이 거부되면 Optional.empty()를 반환하고 예외를 전파하지 않는다")
    void geocode_connectionRefused_returnsEmptyWithoutException() throws IOException {
        mockWebServer.shutdown();

        Optional<BigDecimal[]> result = adapter.geocode("서울시청");

        assertThat(result).isEmpty();
    }
}
