package dev.jazzybyte.onseoul.collector;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.collector.dto.KakaoGeocodingResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

@Slf4j
@Component
public class KakaoGeocodingClient {

    private static final String ADDRESS_SEARCH_PATH = "/v2/local/search/address.json";
    private static final String KEYWORD_SEARCH_PATH = "/v2/local/search/keyword.json";

    private final WebClient kakaoWebClient;
    private final ObjectMapper objectMapper;

    public KakaoGeocodingClient(@Qualifier("kakaoWebClient") WebClient kakaoWebClient,
                                 ObjectMapper objectMapper) {
        this.kakaoWebClient = kakaoWebClient;
        this.objectMapper = objectMapper;
    }

    /**
     * 주소로 좌표 변환 API를 호출하여 좌표를 반환한다.
     *
     * @param placeName 장소명
     * @return [x(경도), y(위도)] 또는 결과 없음/오류 시 Optional.empty()
     */
    public Optional<BigDecimal[]> addressSearch(String placeName) {
        try {
            String body = kakaoWebClient.get()
                    .uri(uriBuilder -> uriBuilder
                            .path(ADDRESS_SEARCH_PATH)
                            .queryParam("query", placeName)
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
            return Optional.of(new BigDecimal[]{
                    new BigDecimal(doc.getX()),
                    new BigDecimal(doc.getY())
            });

        } catch (Exception e) {
            log.warn("카카오 Geocoding 실패 — placeName={}, error={}", placeName, e.getMessage());
            return Optional.empty();
        }
    }

    /**
     * 장소명으로 카카오 키워드 검색 API를 호출하여 좌표를 반환한다.
     *
     * @param keyword 장소명
     * @return [x(경도), y(위도)] 또는 결과 없음/오류 시 Optional.empty()
     */
    public Optional<BigDecimal[]> keywordSearch(String keyword) {
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
            log.debug("카카오 키워드 검색 결과: keyword={}, placeName={}, x={}, y={}",
                    keyword, doc.getAddressName(), doc.getX(), doc.getY());
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
