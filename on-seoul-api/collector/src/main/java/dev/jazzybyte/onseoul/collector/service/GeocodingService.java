package dev.jazzybyte.onseoul.collector.service;

import dev.jazzybyte.onseoul.collector.KakaoGeocodingClient;
import dev.jazzybyte.onseoul.collector.config.KakaoApiProperties;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.repository.PublicServiceReservationRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Slf4j
@Service
@RequiredArgsConstructor
public class GeocodingService {

    private final KakaoGeocodingClient geocodingClient;
    private final PublicServiceReservationRepository repository;
    private final KakaoApiProperties properties;

    /** 장소명 → 좌표 인스턴스 캐시 (동일 장소명 중복 API 호출 방지) */
    private final Map<String, Optional<BigDecimal[]>> coordsCache = new HashMap<>();

    /**
     * coordX 또는 coordY가 null인 레코드에 카카오 키워드 검색으로 좌표를 채운다.
     * {@code kakao.api.key}가 미설정이면 조용히 스킵한다.
     */
    public void fillMissingCoords() {
        if (properties.getKey().isBlank()) {
            log.warn("KAKAO_REST_API_KEY 미설정 — Geocoding sweep 스킵");
            return;
        }

        List<PublicServiceReservation> records = repository.findAllByCoordXIsNullOrCoordYIsNull();
        if (records.isEmpty()) {
            return;
        }

        log.info("Geocoding sweep 시작 — 대상 {}건", records.size());
        int filled = 0;

        for (PublicServiceReservation record : records) {
            String placeName = record.getPlaceName();
            if (placeName == null || placeName.isBlank()) {
                continue;
            }

            Optional<BigDecimal[]> coords = coordsCache.computeIfAbsent(placeName,
                    geocodingClient::keywordSearch);

            if (coords.isPresent()) {
                record.updateCoords(coords.get()[0], coords.get()[1]);
                repository.save(record);
                filled++;
            }
        }

        log.info("Geocoding sweep 완료 — {}건 좌표 보정 (총 대상 {}건)", filled, records.size());
    }
}
