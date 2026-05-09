package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;
import dev.jazzybyte.onseoul.domain.port.out.GeocodingPort;
import dev.jazzybyte.onseoul.domain.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.domain.port.out.SavePublicServicePort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Slf4j
@Service
public class GeocodingService {

    private final GeocodingPort geocodingPort;
    private final LoadPublicServicePort loadPublicServicePort;
    private final SavePublicServicePort savePublicServicePort;

    public GeocodingService(final GeocodingPort geocodingPort,
                            final LoadPublicServicePort loadPublicServicePort,
                            final SavePublicServicePort savePublicServicePort) {
        this.geocodingPort = geocodingPort;
        this.loadPublicServicePort = loadPublicServicePort;
        this.savePublicServicePort = savePublicServicePort;
    }

    /**
     * coordX or coordY가 null인 레코드에 좌표를 채운다.
     * GeocodingPort 구현체가 내부적으로 API 키 유효 여부를 처리한다.
     */
    public void fillMissingCoords() {
        List<PublicServiceReservation> records = loadPublicServicePort.findAllByCoordXIsNullOrCoordYIsNull();
        if (records.isEmpty()) {
            return;
        }

        log.info("Geocoding sweep 시작 — 대상 {}건", records.size());
        Map<String, Optional<BigDecimal[]>> cache = new HashMap<>();
        int filled = 0;

        for (PublicServiceReservation record : records) {
            String placeName = record.getPlaceName();
            if (placeName == null || placeName.isBlank()) {
                continue;
            }

            Optional<BigDecimal[]> coords = cache.computeIfAbsent(placeName, geocodingPort::geocode);

            if (coords.isPresent()) {
                record.updateCoords(coords.get()[0], coords.get()[1]);
                savePublicServicePort.save(record);
                filled++;
            }
        }

        log.info("Geocoding sweep 완료 — {}건 좌표 보정 (총 대상 {}건)", filled, records.size());
    }
}
