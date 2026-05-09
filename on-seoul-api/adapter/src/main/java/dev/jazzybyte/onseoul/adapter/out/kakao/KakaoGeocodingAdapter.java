package dev.jazzybyte.onseoul.adapter.out.kakao;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.domain.port.out.GeocodingPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

@Slf4j
@Component
class KakaoGeocodingAdapter implements GeocodingPort {

    private static final String KEYWORD_SEARCH_PATH = "/v2/local/search/keyword.json";

    private final WebClient kakaoWebClient;
    private final ObjectMapper objectMapper;
    private final KakaoApiProperties properties;

    KakaoGeocodingAdapter(@Qualifier("kakaoWebClient") WebClient kakaoWebClient,
                          ObjectMapper objectMapper,
                          KakaoApiProperties properties) {
        this.kakaoWebClient = kakaoWebClient;
        this.objectMapper = objectMapper;
        this.properties = properties;
    }

    @Override
    public Optional<BigDecimal[]> geocode(String placeName) {
        if (properties.getKey().isBlank()) {
            log.warn("KAKAO_REST_API_KEY 미설정 — Geocoding 스킵: placeName={}", placeName);
            return Optional.empty();
        }
        return keywordSearch(placeName);
    }

    private Optional<BigDecimal[]> keywordSearch(String keyword) {
        try {
            String body = kakaoWebClient.get()
                    .uri(uriBuilder -> uriBuilder
                            .path(KEYWORD_SEARCH_PATH)
                            .queryParam("query", keyword)
                            .queryParam("size", 1)
                            .build())
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();

            if (body == null) {
                return Optional.empty();
            }

            KakaoGeocodingResponse response = objectMapper.readValue(body, KakaoGeocodingResponse.class);
            List<KakaoGeocodingResponse.Document> docs = response.getDocuments();

            if (docs == null || docs.isEmpty()) {
                return Optional.empty();
            }

            KakaoGeocodingResponse.Document doc = docs.get(0);
            log.debug("카카오 키워드 검색 결과: keyword={}, x={}, y={}", keyword, doc.getX(), doc.getY());
            return Optional.of(new BigDecimal[]{
                    new BigDecimal(doc.getX()),
                    new BigDecimal(doc.getY())
            });

        } catch (Exception e) {
            log.warn("카카오 장소검색 API 실패 — placeName={}, error={}", keyword, e.getMessage());
            return Optional.empty();
        }
    }
}
