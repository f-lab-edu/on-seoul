package dev.jazzybyte.onseoul.collection.adapter.out.seoulapi;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.collection.domain.PublicServiceReservation;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.web.reactive.function.client.WebClient;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 서울 열린데이터 광장 Open API 실 연동 테스트.
 *
 * <p>평소에는 {@code @Disabled}로 비활성화한다. 실행 전 환경변수
 * {@code SEOUL_OPEN_API_KEY}를 유효한 인증키로 설정해야 한다.</p>
 *
 * <pre>
 * SEOUL_OPEN_API_KEY=your_key ./gradlew :adapter:test \
 *     --tests "*.SeoulOpenApiAdapterIntegrationTest"
 * </pre>
 */
@Disabled("실 API 연동 테스트 — 실행 시 SEOUL_OPEN_API_KEY 환경변수 필요")
class SeoulOpenApiAdapterIntegrationTest {

    private SeoulOpenApiAdapter adapter;

    @BeforeEach
    void setUp() {
        String apiKey = System.getenv("SEOUL_OPEN_API_KEY");
        assertThat(apiKey)
                .as("SEOUL_OPEN_API_KEY 환경변수가 설정되어 있어야 합니다.")
                .isNotBlank();

        SeoulApiProperties properties = new SeoulApiProperties(apiKey);
        properties.setPageSize(200);
        properties.setMaxRetries(2);
        properties.setMaxBackoffSeconds(3);

        WebClient webClient = WebClient.builder()
                .baseUrl(properties.getBaseUrl())
                .codecs(configurer ->
                        configurer.defaultCodecs().maxInMemorySize(20 * 1024 * 1024))
                .build();

        adapter = new SeoulOpenApiAdapter(
                webClient, properties, new ObjectMapper(), new PublicServiceRowMapper());
    }

    @ParameterizedTest(name = "[실연동] {0} 수집 → 데이터 존재 및 serviceId 비어있지 않음")
    @ValueSource(strings = {
            "ListPublicReservationSport",
            "ListPublicReservationInstitution",
            "ListPublicReservationEducation",
            "ListPublicReservationCulture",
            "ListPublicReservationMedical"
    })
    void fetchAll_allCategories_returnsNonEmptyWithValidServiceIds(String serviceName) {
        List<PublicServiceReservation> result = adapter.fetchAll(serviceName);

        assertThat(result)
                .as("서비스명 '%s' 수집 결과는 비어있지 않아야 합니다.", serviceName)
                .isNotEmpty();

        assertThat(result)
                .as("모든 항목의 serviceId는 비어있지 않아야 합니다.")
                .allSatisfy(r -> assertThat(r.getServiceId()).isNotBlank());
    }

    @Test
    @DisplayName("[실연동] 문화행사 첫 페이지 수집 — row 수가 pageSize(200) 이하")
    void fetchPage_culture_firstPage_rowCountWithinPageSize() {
        SeoulApiResponse response = adapter.fetchPage("ListPublicReservationCulture", 1, 200);

        assertThat(response.getRows())
                .as("첫 페이지 row 수는 pageSize(200) 이하여야 합니다.")
                .hasSizeLessThanOrEqualTo(200);

        assertThat(response.getListTotalCount())
                .as("list_total_count는 0보다 커야 합니다.")
                .isGreaterThan(0);

        System.out.printf("문화행사 전체 건수: %d건%n", response.getListTotalCount());
    }

    @Test
    @DisplayName("[실연동] 체육시설 수집 후 주요 도메인 필드(placeName, areaName) 매핑 확인")
    void fetchAll_sport_domainFieldsMapped() {
        List<PublicServiceReservation> result = adapter.fetchAll("ListPublicReservationSport");

        assertThat(result).isNotEmpty();

        PublicServiceReservation first = result.getFirst();
        assertThat(first.getServiceId()).isNotBlank();
        assertThat(first.getServiceName()).isNotBlank();
        assertThat(first.getPlaceName()).isNotBlank();
        assertThat(first.getAreaName()).isNotBlank();

        System.out.printf(
                "체육시설 첫 번째 항목: serviceId=%s, serviceName=%s, placeName=%s, areaName=%s%n",
                first.getServiceId(), first.getServiceName(),
                first.getPlaceName(), first.getAreaName());
    }

    @Test
    @DisplayName("[실연동] 전체 5개 카테고리 합산 수집 건수 출력")
    void fetchAll_allCategories_printTotalCount() {
        String[] serviceNames = {
                "ListPublicReservationSport",
                "ListPublicReservationInstitution",
                "ListPublicReservationEducation",
                "ListPublicReservationCulture",
                "ListPublicReservationMedical"
        };

        int total = 0;
        for (String serviceName : serviceNames) {
            List<PublicServiceReservation> result = adapter.fetchAll(serviceName);
            System.out.printf("  %-40s : %4d건%n", serviceName, result.size());
            total += result.size();
        }
        System.out.printf("  %-40s : %4d건%n", "[합계]", total);

        assertThat(total)
                .as("5개 카테고리 합산 수집 건수는 1건 이상이어야 합니다.")
                .isGreaterThan(0);
    }
}
