package dev.jazzybyte.onseoul.collector;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.collector.config.KakaoApiProperties;
import lombok.extern.slf4j.Slf4j;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;

import java.math.BigDecimal;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 카카오 키워드 검색 API 실제 호출 테스트.
 *
 * <p>{@code KAKAO_REST_API_KEY} 환경변수가 설정된 상태에서 {@code @Disabled}를 제거하고 실행한다.</p>
 */
@Slf4j
@Disabled("카카오 API 실제 호출 — 환경변수 KAKAO_REST_API_KEY 설정 후 @Disabled 제거하여 수동 실행")
class KakaoGeocodingClientIntegrationTest {

    private KakaoGeocodingClient client;

    @BeforeEach
    void setUp() {
        String apiKey = System.getenv("KAKAO_REST_API_KEY");
        assertThat(apiKey)
                .as("KAKAO_REST_API_KEY 환경변수가 설정되어 있어야 합니다")
                .isNotBlank();

        KakaoApiProperties properties = new KakaoApiProperties();
        properties.setKey(apiKey);
        // baseUrl 기본값 "https://dapi.kakao.com" 사용

        WebClient kakaoWebClient = WebClient.builder()
                .baseUrl(properties.getBaseUrl())
                .defaultHeader("Authorization", "KakaoAK " + properties.getKey())
                .build();

        client = new KakaoGeocodingClient(kakaoWebClient, new ObjectMapper());
    }

    @Test
    @DisplayName("주소 검색시 좌표를 반환한다")
    void addressSearch_real_place() {
        String placeName = "서울특별시 뚝섬로 273";
        Optional<BigDecimal[]> result = client.addressSearch(placeName);

        assertThat(result).isPresent();
        BigDecimal x = result.get()[0];
        BigDecimal y = result.get()[1];

        log.info("장소: {}\n  x(경도): {}\n  y(위도): {}", placeName, x, y);

        // 서울 시내 경도/위도 범위 검증
        assertThat(x).isBetween(new BigDecimal("125.0"), new BigDecimal("128.0"));
        assertThat(y).isBetween(new BigDecimal("30.0"), new BigDecimal("40.0"));
    }

    @Test
    @DisplayName("키워드 검색시 좌표를 반환한다")
    void keywordSearch_real_place() {
        String keyword = "서울특별시 산악문화체험센터";
        Optional<BigDecimal[]> result = client.keywordSearch(keyword);

        assertThat(result).isPresent();
        BigDecimal x = result.get()[0];
        BigDecimal y = result.get()[1];

        log.info("키워드: {}\n  x(경도): {}\n  y(위도): {}", keyword, x, y);

        // 서울 시내 경도/위도 범위 검증
        assertThat(x).isBetween(new BigDecimal("125.0"), new BigDecimal("128.0"));
        assertThat(y).isBetween(new BigDecimal("30.0"), new BigDecimal("40.0"));
    }

    @Test
    @DisplayName("존재하지 않는 장소명은 Optional.empty()를 반환한다")
    void keywordSearch_nonexistent_place_returns_empty() {
        Optional<BigDecimal[]> result = client.keywordSearch("이런장소는절대없을거야_xQzPf9");

        assertThat(result).isEmpty();
        log.info("존재하지 않는 장소 검색 → Optional.empty() 확인");
    }

}
