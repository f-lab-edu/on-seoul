package dev.jazzybyte.onseoul.collector;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.collector.config.SeoulApiProperties;
import dev.jazzybyte.onseoul.collector.dto.PublicServiceRow;
import dev.jazzybyte.onseoul.collector.dto.SeoulApiResponse;
import dev.jazzybyte.onseoul.collector.exception.SeoulApiException;
import dev.jazzybyte.onseoul.collector.exception.SeoulApiServerException;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.*;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.util.retry.Retry;

import java.io.IOException;
import java.util.List;

import static org.assertj.core.api.Assertions.*;

class SeoulOpenApiClientTest {

    private static MockWebServer mockWebServer;
    private SeoulOpenApiClient client;
    private SeoulApiProperties properties;

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
        properties = new SeoulApiProperties("TEST_API_KEY");
        properties.setBaseUrl(mockWebServer.url("/").toString());
        properties.setPageSize(1000);
        properties.setMaxRetries(3);

        WebClient webClient = WebClient.builder()
                .baseUrl(properties.getBaseUrl())
                .build();

        // 테스트에서는 즉시 재시도 (backoff 없음) — 5xx(SeoulApiServerException)만 재시도
        client = new SeoulOpenApiClient(webClient, properties, new ObjectMapper(),
                                        Retry.max(3).filter(ex -> ex instanceof SeoulApiServerException));
    }

    // ────────────────────────────────────────────
    // fetchAll — 페이지네이션
    // ────────────────────────────────────────────

    @Test
    @DisplayName("전체 건수가 pageSize 이하이면 1페이지로 수집이 완료된다")
    void fetchAll_single_page() throws InterruptedException {
        mockWebServer.enqueue(apiResponse("ListPublicReservationCulture", 2,
                row("SVC-001", "접수중"), row("SVC-002", "마감")));

        List<PublicServiceRow> result = client.fetchAll("ListPublicReservationCulture");

        assertThat(result).hasSize(2);
        assertThat(result).extracting(PublicServiceRow::getSvcid)
                          .containsExactly("SVC-001", "SVC-002");

        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getPath()).contains("/TEST_API_KEY/json/ListPublicReservationCulture/1/1000/");
    }

    @Test
    @DisplayName("전체 건수가 pageSize를 초과하면 여러 페이지를 순차적으로 수집한다")
    void fetchAll_multiple_pages() {
        // pageSize=3, 총 5건 → 2페이지 필요 (1-3, 4-5)
        SeoulApiProperties smallPage = new SeoulApiProperties("TEST_API_KEY");
        smallPage.setBaseUrl(mockWebServer.url("/").toString());
        smallPage.setPageSize(3);

        SeoulOpenApiClient smallClient = new SeoulOpenApiClient(
                WebClient.builder().baseUrl(smallPage.getBaseUrl()).build(),
                smallPage, new ObjectMapper(),
                Retry.max(3).filter(ex -> ex instanceof SeoulApiServerException));

        mockWebServer.enqueue(apiResponse("ListPublicReservationSport", 5,
                row("SVC-1", "접수중"), row("SVC-2", "접수중"), row("SVC-3", "접수중")));
        mockWebServer.enqueue(apiResponse("ListPublicReservationSport", 5,
                row("SVC-4", "접수중"), row("SVC-5", "마감")));

        List<PublicServiceRow> result = smallClient.fetchAll("ListPublicReservationSport");

        assertThat(result).hasSize(5);
        assertThat(result).extracting(PublicServiceRow::getSvcid)
                          .containsExactly("SVC-1", "SVC-2", "SVC-3", "SVC-4", "SVC-5");
    }

    @Test
    @DisplayName("API 키가 요청 경로에 포함된다")
    void fetchAll_includes_api_key_in_path() throws InterruptedException {
        mockWebServer.enqueue(apiResponse("ListPublicReservationCulture", 1, row("SVC-001", "접수중")));

        client.fetchAll("ListPublicReservationCulture");

        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getPath()).startsWith("/TEST_API_KEY/");
    }

    // ────────────────────────────────────────────
    // fetchPage — 재시도
    // ────────────────────────────────────────────

    @Test
    @DisplayName("5xx 오류 후 재시도하여 성공하면 정상 응답을 반환한다")
    void fetchPage_retries_on_5xx_and_succeeds() {
        int before = mockWebServer.getRequestCount();

        mockWebServer.enqueue(new MockResponse().setResponseCode(500));
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));
        mockWebServer.enqueue(apiResponse("ListPublicReservationCulture", 1, row("SVC-001", "접수중")));

        SeoulApiResponse response = client.fetchPage("ListPublicReservationCulture", 1, 1000);

        assertThat(response.getRows()).hasSize(1);
        assertThat(mockWebServer.getRequestCount() - before).isEqualTo(3);
    }

    @Test
    @DisplayName("5xx 오류가 최대 재시도 횟수를 초과하면 예외가 발생한다")
    void fetchPage_throws_after_max_retries_exceeded() {
        int before = mockWebServer.getRequestCount();

        mockWebServer.enqueue(new MockResponse().setResponseCode(500));
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));

        assertThatThrownBy(() -> client.fetchPage("ListPublicReservationCulture", 1, 1000))
                .isInstanceOf(Exception.class);

        // 초기 1회 + 재시도 3회 = 총 4회
        assertThat(mockWebServer.getRequestCount() - before).isEqualTo(4);
    }

    @Test
    @DisplayName("4xx 오류는 재시도 없이 즉시 예외가 발생한다")
    void fetchPage_throws_immediately_on_4xx() {
        int before = mockWebServer.getRequestCount();

        mockWebServer.enqueue(new MockResponse().setResponseCode(401));

        assertThatThrownBy(() -> client.fetchPage("ListPublicReservationCulture", 1, 1000))
                .isInstanceOf(Exception.class);

        assertThat(mockWebServer.getRequestCount() - before).isEqualTo(1);
    }

    // ────────────────────────────────────────────
    // parseResponse — 응답 파싱
    // ────────────────────────────────────────────

    @Test
    @DisplayName("API 오류 코드(INFO-000 외)를 받으면 SeoulApiException이 발생한다")
    void fetchPage_throws_on_error_result_code() {
        mockWebServer.enqueue(errorResponse("ListPublicReservationCulture", "ERROR-001", "등록되지 않은 인증키입니다."));

        assertThatThrownBy(() -> client.fetchPage("ListPublicReservationCulture", 1, 1000))
                .isInstanceOf(SeoulApiException.class)
                .hasMessageContaining("ERROR-001");
    }

    @Test
    @DisplayName("INFO-200(데이터 없음) 응답은 예외 없이 빈 목록을 반환한다")
    void fetchPage_returns_empty_list_on_info_200() {
        mockWebServer.enqueue(noDataResponse("ListPublicReservationCulture"));

        SeoulApiResponse response = client.fetchPage("ListPublicReservationCulture", 1, 1000);

        assertThat(response.getRows()).isEmpty();
        assertThat(response.getListTotalCount()).isZero();
    }

    @Test
    @DisplayName("INFO-200 응답 시 fetchAll은 빈 목록을 반환한다")
    void fetchAll_returns_empty_list_on_info_200() {
        mockWebServer.enqueue(noDataResponse("ListPublicReservationCulture"));

        List<PublicServiceRow> result = client.fetchAll("ListPublicReservationCulture");

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("응답 JSON에 서비스명 키가 없으면 SeoulApiException이 발생한다")
    void fetchPage_throws_when_service_key_missing() {
        mockWebServer.enqueue(new MockResponse()
                .setHeader(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
                .setBody("{\"UnknownKey\": {}}"));

        assertThatThrownBy(() -> client.fetchPage("ListPublicReservationCulture", 1, 1000))
                .isInstanceOf(SeoulApiException.class)
                .hasMessageContaining("ListPublicReservationCulture");
    }

    @Test
    @DisplayName("빈 응답 body를 받으면 SeoulApiException이 발생한다")
    void fetchPage_throws_on_empty_body() {
        mockWebServer.enqueue(new MockResponse()
                .setHeader(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
                .setBody(""));

        assertThatThrownBy(() -> client.fetchPage("ListPublicReservationCulture", 1, 1000))
                .isInstanceOf(SeoulApiException.class);
    }

    // ────────────────────────────────────────────
    // 헬퍼 메서드
    // ────────────────────────────────────────────

    private MockResponse apiResponse(String serviceName, int totalCount, String... rowJsons) {
        String rows = String.join(",", rowJsons);
        String body = String.format(
                "{\"%s\": {\"list_total_count\": %d, \"RESULT\": {\"CODE\": \"INFO-000\", \"MESSAGE\": \"정상 처리됩니다\"}, \"row\": [%s]}}",
                serviceName, totalCount, rows);
        return new MockResponse()
                .setHeader(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
                .setBody(body);
    }

    private MockResponse noDataResponse(String serviceName) {
        String body = String.format(
                "{\"%s\": {\"list_total_count\": 0, \"RESULT\": {\"CODE\": \"INFO-200\", \"MESSAGE\": \"해당하는 데이터가 없습니다.\"}, \"row\": []}}",
                serviceName);
        return new MockResponse()
                .setHeader(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
                .setBody(body);
    }

    private MockResponse errorResponse(String serviceName, String code, String message) {
        String body = String.format(
                "{\"%s\": {\"list_total_count\": 0, \"RESULT\": {\"CODE\": \"%s\", \"MESSAGE\": \"%s\"}, \"row\": []}}",
                serviceName, code, message);
        return new MockResponse()
                .setHeader(HttpHeaders.CONTENT_TYPE, MediaType.APPLICATION_JSON_VALUE)
                .setBody(body);
    }

    private String row(String svcid, String status) {
        return String.format("{\"SVCID\": \"%s\", \"SVCSTATNM\": \"%s\", \"SVCNM\": \"테스트 서비스\", \"AREANM\": \"종로구\"}",
                svcid, status);
    }
}
