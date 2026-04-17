package dev.jazzybyte.onseoul.collector.service;

import dev.jazzybyte.onseoul.collector.KakaoGeocodingClient;
import dev.jazzybyte.onseoul.collector.config.KakaoApiProperties;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.repository.PublicServiceReservationRepository;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class GeocodingServiceTest {

    @Mock private KakaoGeocodingClient geocodingClient;
    @Mock private PublicServiceReservationRepository repository;
    @Mock private KakaoApiProperties properties;

    @InjectMocks
    private GeocodingService geocodingService;

    @Test
    @DisplayName("API 키가 비어있으면 sweep을 스킵한다")
    void skips_when_api_key_is_blank() {
        when(properties.getKey()).thenReturn("");

        geocodingService.fillMissingCoords();

        verifyNoInteractions(repository, geocodingClient);
    }

    @Test
    @DisplayName("coordX/Y가 null인 레코드에 카카오 API로 좌표를 채운다")
    void fills_coords_for_null_coord_records() {
        when(properties.getKey()).thenReturn("valid-key");
        PublicServiceReservation record = PublicServiceReservation.builder()
                .serviceId("SVC001").serviceName("테스트 서비스").serviceStatus("접수중")
                .placeName("서울시청")
                .build();
        // coordX, coordY는 null (builder에서 설정하지 않음)
        when(repository.findAllByCoordXIsNullOrCoordYIsNull()).thenReturn(List.of(record));
        when(geocodingClient.keywordSearch("서울시청"))
                .thenReturn(Optional.of(new BigDecimal[]{new BigDecimal("126.9784"), new BigDecimal("37.5665")}));

        geocodingService.fillMissingCoords();

        verify(repository).save(record);
        assertThat(record.getCoordX()).isEqualByComparingTo("126.9784");
        assertThat(record.getCoordY()).isEqualByComparingTo("37.5665");
    }

    @Test
    @DisplayName("카카오 API 결과가 없는 장소명은 저장하지 않는다")
    void skips_record_when_geocoding_returns_empty() {
        when(properties.getKey()).thenReturn("valid-key");
        PublicServiceReservation record = PublicServiceReservation.builder()
                .serviceId("SVC001").serviceName("테스트 서비스").serviceStatus("접수중")
                .placeName("알수없는장소")
                .build();
        when(repository.findAllByCoordXIsNullOrCoordYIsNull()).thenReturn(List.of(record));
        when(geocodingClient.keywordSearch("알수없는장소")).thenReturn(Optional.empty());

        geocodingService.fillMissingCoords();

        verify(repository, never()).save(any());
    }

    @Test
    @DisplayName("동일 장소명은 API를 한 번만 호출한다 (캐시)")
    void caches_result_for_same_place_name() {
        when(properties.getKey()).thenReturn("valid-key");
        PublicServiceReservation r1 = PublicServiceReservation.builder()
                .serviceId("SVC001").serviceName("테스트1").serviceStatus("접수중")
                .placeName("서울시청").build();
        PublicServiceReservation r2 = PublicServiceReservation.builder()
                .serviceId("SVC002").serviceName("테스트2").serviceStatus("접수중")
                .placeName("서울시청").build();
        when(repository.findAllByCoordXIsNullOrCoordYIsNull()).thenReturn(List.of(r1, r2));
        when(geocodingClient.keywordSearch("서울시청"))
                .thenReturn(Optional.of(new BigDecimal[]{new BigDecimal("126.9784"), new BigDecimal("37.5665")}));

        geocodingService.fillMissingCoords();

        verify(geocodingClient, times(1)).keywordSearch("서울시청"); // 캐시로 인해 1회만 호출
        verify(repository, times(2)).save(any());
    }
}
