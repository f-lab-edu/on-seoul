package dev.jazzybyte.onseoul.collection.adapter.out.seoulapi;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.collection.domain.PublicServiceReservation;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.mockwebserver.RecordedRequest;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.util.retry.Retry;

import java.io.IOException;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class SeoulOpenApiAdapterTest {

    private static final String SERVICE_NAME = "ListPublicReservationCulture";

    private MockWebServer mockWebServer;
    private SeoulOpenApiAdapter adapter;
    private SeoulApiProperties properties;

    @BeforeEach
    void setUp() throws IOException {
        mockWebServer = new MockWebServer();
        mockWebServer.start();

        properties = new SeoulApiProperties("test-key");
        properties.setBaseUrl(mockWebServer.url("/").toString());
        properties.setPageSize(200);
        properties.setMaxRetries(3);
        properties.setMaxBackoffSeconds(1);

        WebClient webClient = WebClient.builder()
                .baseUrl(mockWebServer.url("/").toString())
                .build();

        ObjectMapper objectMapper = new ObjectMapper();
        PublicServiceRowMapper rowMapper = new PublicServiceRowMapper();

        Retry noRetry = Retry.max(0)
                .filter(ex -> ex instanceof SeoulApiServerException);

        adapter = new SeoulOpenApiAdapter(webClient, properties, objectMapper, rowMapper, noRetry);
    }

    @AfterEach
    void tearDown() throws IOException {
        mockWebServer.shutdown();
    }

    private String buildResponse(int totalCount, int rowCount, int startSvcId) {
        StringBuilder rows = new StringBuilder();
        for (int i = 0; i < rowCount; i++) {
            if (i > 0) rows.append(",");
            rows.append(buildRow("SVC" + (startSvcId + i), "서비스" + (startSvcId + i)));
        }

        return "{\"%s\":{\"list_total_count\":%d,\"RESULT\":{\"CODE\":\"INFO-000\",\"MESSAGE\":\"정상 처리되었습니다.\"},\"row\":[%s]}}"
                .formatted(SERVICE_NAME, totalCount, rows);
    }

    private String buildRow(String svcId, String svcNm) {
        return """
                {
                    "SVCID": "%s",
                    "SVCNM": "%s",
                    "GUBUN": "문화",
                    "MAXCLASSNM": "문화행사",
                    "MINCLASSNM": "공연",
                    "SVCSTATNM": "접수중",
                    "PAYATNM": "무료",
                    "PLACENM": "서울시립미술관",
                    "AREANM": "중구",
                    "X": "126.9779",
                    "Y": "37.5665",
                    "SVCOPNBGNDT": "2025-01-01 09:00:00",
                    "SVCOPNENDDT": "2025-12-31 18:00:00",
                    "RCPTBGNDT": "2025-01-01 09:00:00",
                    "RCPTENDDT": "2025-06-30 18:00:00",
                    "V_MIN": "09:00",
                    "V_MAX": "18:00",
                    "REVSTDDAYNM": "",
                    "REVSTDDAY": ""
                }
                """.formatted(svcId, svcNm);
    }

    private String buildNoDataResponse() {
        return "{\"%s\":{\"list_total_count\":0,\"RESULT\":{\"CODE\":\"INFO-200\",\"MESSAGE\":\"해당하는 데이터가 없습니다.\"},\"row\":[]}}"
                .formatted(SERVICE_NAME);
    }

    @Test
    @DisplayName("전체 건수가 pageSize 미만이면 1회 API 호출로 완료된다")
    void fetchAll_singlePage_callsApiOnce() throws InterruptedException {
        int totalCount = 100;
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(buildResponse(totalCount, totalCount, 1)));

        List<PublicServiceReservation> result = adapter.fetchAll(SERVICE_NAME);

        assertThat(result).hasSize(totalCount);
        assertThat(mockWebServer.getRequestCount()).isEqualTo(1);

        RecordedRequest request = mockWebServer.takeRequest();
        assertThat(request.getPath()).contains("/1/");
    }

    @Test
    @DisplayName("전체 건수가 pageSize를 초과하면 2회 API 호출이 발생한다")
    void fetchAll_exactlyTwoPages_callsApiTwice() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(buildResponse(201, 200, 1)));
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(buildResponse(201, 1, 201)));

        List<PublicServiceReservation> result = adapter.fetchAll(SERVICE_NAME);

        assertThat(result).hasSize(201);
        assertThat(mockWebServer.getRequestCount()).isEqualTo(2);
    }

    @Test
    @DisplayName("list_total_count가 0이면 빈 리스트를 반환한다")
    void fetchAll_totalCountZero_returnsEmptyList() {
        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(buildNoDataResponse()));

        List<PublicServiceReservation> result = adapter.fetchAll(SERVICE_NAME);

        assertThat(result).isEmpty();
        assertThat(mockWebServer.getRequestCount()).isEqualTo(1);
    }

    @Test
    @DisplayName("서울 API가 5xx를 반환하면 재시도 후 예외가 발생한다")
    void fetchAll_serverError_throwsAfterRetry() {
        mockWebServer.enqueue(new MockResponse().setResponseCode(500));

        assertThatThrownBy(() -> adapter.fetchAll(SERVICE_NAME))
                .isInstanceOf(Exception.class);
    }

    @Test
    @DisplayName("3회 재시도 설정 시 5xx가 반복되면 3회 시도 후 예외가 발생한다")
    void fetchAll_serverError_retriesThreeTimes() throws IOException {
        Retry threeRetries = Retry.max(3)
                .filter(ex -> ex instanceof SeoulApiServerException);

        WebClient retryWebClient = WebClient.builder()
                .baseUrl(mockWebServer.url("/").toString())
                .build();

        SeoulOpenApiAdapter retryAdapter = new SeoulOpenApiAdapter(
                retryWebClient, properties, new ObjectMapper(),
                new PublicServiceRowMapper(), threeRetries);

        for (int i = 0; i < 4; i++) {
            mockWebServer.enqueue(new MockResponse().setResponseCode(500));
        }

        assertThatThrownBy(() -> retryAdapter.fetchAll(SERVICE_NAME))
                .isInstanceOf(Exception.class);

        assertThat(mockWebServer.getRequestCount()).isEqualTo(4);
    }

    @Test
    @DisplayName("RowMapper가 Optional.empty()를 반환한 row는 결과 리스트에서 제외된다")
    void fetchAll_rowMapperReturnsEmpty_excludedFromResult() {
        String responseWithInvalidRow = """
                {"%s":{
                    "list_total_count":3,
                    "RESULT":{"CODE":"INFO-000","MESSAGE":"정상 처리되었습니다."},
                    "row":[
                        {"SVCID":"VALID1","SVCNM":"서비스1","GUBUN":"문화","MAXCLASSNM":"","MINCLASSNM":"","SVCSTATNM":"","PAYATNM":"","PLACENM":"","AREANM":""},
                        {"SVCID":null,"SVCNM":"누락된 서비스","GUBUN":"","MAXCLASSNM":"","MINCLASSNM":"","SVCSTATNM":"","PAYATNM":"","PLACENM":"","AREANM":""},
                        {"SVCID":"VALID2","SVCNM":"서비스2","GUBUN":"체육","MAXCLASSNM":"","MINCLASSNM":"","SVCSTATNM":"","PAYATNM":"","PLACENM":"","AREANM":""}
                    ]
                }}
                """.formatted(SERVICE_NAME);

        mockWebServer.enqueue(new MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(responseWithInvalidRow));

        List<PublicServiceReservation> result = adapter.fetchAll(SERVICE_NAME);

        assertThat(result).hasSize(2);
        assertThat(result).extracting(PublicServiceReservation::getServiceId)
                .containsExactly("VALID1", "VALID2");
    }
}
