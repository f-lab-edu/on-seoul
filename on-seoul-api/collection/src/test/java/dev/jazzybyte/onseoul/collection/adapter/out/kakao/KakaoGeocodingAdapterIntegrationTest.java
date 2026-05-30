package dev.jazzybyte.onseoul.collection.adapter.out.kakao;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

import java.math.BigDecimal;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 카카오 로컬 API 실 연동 테스트.
 *
 * <p>평소에는 {@code @Disabled}로 비활성화한다. 실행 전 환경변수
 * {@code KAKAO_REST_API_KEY}를 유효한 REST API 키로 설정해야 한다.</p>
 *
 * <pre>
 * KAKAO_REST_API_KEY=your_key ./gradlew :collection:test \
 *     --tests "*.KakaoGeocodingAdapterIntegrationTest"
 * </pre>
 */
@Disabled("실 API 연동 테스트 — 실행 시 KAKAO_REST_API_KEY 환경변수 필요")
class KakaoGeocodingAdapterIntegrationTest {

    private KakaoGeocodingAdapter adapter;

    @BeforeEach
    void setUp() {
        String apiKey = System.getenv("KAKAO_REST_API_KEY");
        assertThat(apiKey)
                .as("KAKAO_REST_API_KEY 환경변수가 설정되어 있어야 합니다.")
                .isNotBlank();

        KakaoApiProperties properties = new KakaoApiProperties();
        properties.setKey(apiKey);

        WebClient webClient = WebClient.builder()
                .baseUrl(properties.getBaseUrl())
                .defaultHeader("Authorization", "KakaoAK " + apiKey)
                .build();

        adapter = new KakaoGeocodingAdapter(webClient, new ObjectMapper(), properties);
    }

    @Test
    @DisplayName("[실연동] 서울시청 → 좌표 반환")
    void geocode_seoulCityHall_returnsCoordinates() {
        Optional<BigDecimal[]> result = adapter.geocode("서울시청");

        assertThat(result).isPresent();
        BigDecimal[] coords = result.get();

        assertThat(coords[0]).isBetween(new BigDecimal("126.90"), new BigDecimal("127.05"));
        assertThat(coords[1]).isBetween(new BigDecimal("37.50"), new BigDecimal("37.62"));

        System.out.printf("서울시청 좌표: x(경도)=%s, y(위도)=%s%n", coords[0], coords[1]);
    }

    @Test
    @DisplayName("[실연동] 존재하지 않는 장소 → Optional.empty()")
    void geocode_nonExistentPlace_returnsEmpty() {
        Optional<BigDecimal[]> result = adapter.geocode("절대존재하지않는장소XYZABC987");

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("[실연동] 랜드마크 검색 → 좌표 범위(서울 시내) 검증")
    void geocode_landmark_returnsSeoulBoundedCoordinates() {
        Optional<BigDecimal[]> result = adapter.geocode("경복궁");

        assertThat(result).isPresent();
        BigDecimal[] coords = result.get();

        assertThat(coords[0])
                .as("경도(x)는 서울 범위 내여야 합니다.")
                .isBetween(new BigDecimal("126.70"), new BigDecimal("127.20"));
        assertThat(coords[1])
                .as("위도(y)는 서울 범위 내여야 합니다.")
                .isBetween(new BigDecimal("37.40"), new BigDecimal("37.70"));

        System.out.printf("경복궁 좌표: x(경도)=%s, y(위도)=%s%n", coords[0], coords[1]);
    }
}
