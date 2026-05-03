package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;
import dev.jazzybyte.onseoul.domain.port.out.GeocodingPort;
import dev.jazzybyte.onseoul.domain.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.domain.port.out.SavePublicServicePort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
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

    @Mock private GeocodingPort geocodingPort;
    @Mock private LoadPublicServicePort loadPublicServicePort;
    @Mock private SavePublicServicePort savePublicServicePort;

    private GeocodingService service;

    @BeforeEach
    void setUp() {
        service = new GeocodingService(geocodingPort, loadPublicServicePort, savePublicServicePort);
    }

    // ── 헬퍼 ─────────────────────────────────────────────────────────

    private PublicServiceReservation withoutCoords(String serviceId, String placeName) {
        return PublicServiceReservation.builder()
                .serviceId(serviceId)
                .serviceName("서비스-" + serviceId)
                .serviceStatus("접수중")
                .placeName(placeName)
                .coordX(null)
                .coordY(null)
                .build();
    }

    private BigDecimal[] coords(double x, double y) {
        return new BigDecimal[]{BigDecimal.valueOf(x), BigDecimal.valueOf(y)};
    }

    // ── 좌표 누락 레코드 없음 ─────────────────────────────────────────

    @Test
    @DisplayName("좌표 누락 레코드가 없으면 geocodingPort를 호출하지 않는다")
    void fillMissingCoords_noRecords_doesNotCallGeocodingPort() {
        when(loadPublicServicePort.findAllByCoordXIsNullOrCoordYIsNull()).thenReturn(List.of());

        service.fillMissingCoords();

        verifyNoInteractions(geocodingPort, savePublicServicePort);
    }

    // ── placeName null/blank 스킵 ────────────────────────────────────

    @Test
    @DisplayName("placeName이 null인 레코드는 geocoding 호출 없이 스킵한다")
    void fillMissingCoords_nullPlaceName_skipsRecord() {
        PublicServiceReservation record = withoutCoords("SVC-001", null);
        when(loadPublicServicePort.findAllByCoordXIsNullOrCoordYIsNull()).thenReturn(List.of(record));

        service.fillMissingCoords();

        verifyNoInteractions(geocodingPort);
        verify(savePublicServicePort, never()).save(any());
    }

    @Test
    @DisplayName("placeName이 빈 문자열인 레코드는 geocoding 호출 없이 스킵한다")
    void fillMissingCoords_blankPlaceName_skipsRecord() {
        PublicServiceReservation record = withoutCoords("SVC-002", "  ");
        when(loadPublicServicePort.findAllByCoordXIsNullOrCoordYIsNull()).thenReturn(List.of(record));

        service.fillMissingCoords();

        verifyNoInteractions(geocodingPort);
        verify(savePublicServicePort, never()).save(any());
    }

    // ── geocoding 성공 ────────────────────────────────────────────────

    @Test
    @DisplayName("geocoding 성공 시 좌표를 갱신하고 save()를 호출한다")
    void fillMissingCoords_geocodingSuccess_updatesAndSaves() {
        PublicServiceReservation record = withoutCoords("SVC-003", "서울시청");
        when(loadPublicServicePort.findAllByCoordXIsNullOrCoordYIsNull()).thenReturn(List.of(record));
        when(geocodingPort.geocode("서울시청")).thenReturn(Optional.of(coords(126.978, 37.566)));

        service.fillMissingCoords();

        assertThat(record.getCoordX()).isEqualByComparingTo(BigDecimal.valueOf(126.978));
        assertThat(record.getCoordY()).isEqualByComparingTo(BigDecimal.valueOf(37.566));
        verify(savePublicServicePort).save(record);
    }

    // ── geocoding 실패(empty) ─────────────────────────────────────────

    @Test
    @DisplayName("geocoding 결과가 empty이면 좌표를 갱신하지 않고 save()를 호출하지 않는다")
    void fillMissingCoords_geocodingReturnsEmpty_doesNotSave() {
        PublicServiceReservation record = withoutCoords("SVC-004", "존재하지않는장소xyz");
        when(loadPublicServicePort.findAllByCoordXIsNullOrCoordYIsNull()).thenReturn(List.of(record));
        when(geocodingPort.geocode("존재하지않는장소xyz")).thenReturn(Optional.empty());

        service.fillMissingCoords();

        assertThat(record.getCoordX()).isNull();
        assertThat(record.getCoordY()).isNull();
        verify(savePublicServicePort, never()).save(any());
    }

    // ── 캐시 동작 ─────────────────────────────────────────────────────

    @Test
    @DisplayName("동일한 placeName을 가진 레코드가 여러 개여도 geocodingPort는 1회만 호출한다")
    void fillMissingCoords_samePlaceName_geocodingCalledOnce() {
        PublicServiceReservation record1 = withoutCoords("SVC-005", "올림픽공원");
        PublicServiceReservation record2 = withoutCoords("SVC-006", "올림픽공원");
        PublicServiceReservation record3 = withoutCoords("SVC-007", "올림픽공원");

        when(loadPublicServicePort.findAllByCoordXIsNullOrCoordYIsNull())
                .thenReturn(List.of(record1, record2, record3));
        when(geocodingPort.geocode("올림픽공원")).thenReturn(Optional.of(coords(127.123, 37.521)));

        service.fillMissingCoords();

        // 캐시 덕분에 API는 1회만 호출
        verify(geocodingPort, times(1)).geocode("올림픽공원");
        // 3개 모두 좌표 채움
        verify(savePublicServicePort, times(3)).save(any());
    }

    @Test
    @DisplayName("다른 placeName이면 각각 geocodingPort를 호출한다")
    void fillMissingCoords_differentPlaceNames_eachCalledOnce() {
        PublicServiceReservation record1 = withoutCoords("SVC-008", "광화문");
        PublicServiceReservation record2 = withoutCoords("SVC-009", "한강공원");

        when(loadPublicServicePort.findAllByCoordXIsNullOrCoordYIsNull())
                .thenReturn(List.of(record1, record2));
        when(geocodingPort.geocode("광화문")).thenReturn(Optional.of(coords(126.977, 37.576)));
        when(geocodingPort.geocode("한강공원")).thenReturn(Optional.of(coords(126.994, 37.528)));

        service.fillMissingCoords();

        verify(geocodingPort).geocode("광화문");
        verify(geocodingPort).geocode("한강공원");
        verify(savePublicServicePort, times(2)).save(any());
    }

    // ── 혼합 케이스 ───────────────────────────────────────────────────

    @Test
    @DisplayName("성공/실패/스킵이 혼합돼도 성공한 레코드만 저장된다")
    void fillMissingCoords_mixed_onlySuccessfulRecordsSaved() {
        PublicServiceReservation success = withoutCoords("SVC-010", "경복궁");
        PublicServiceReservation fail = withoutCoords("SVC-011", "없는장소");
        PublicServiceReservation skip = withoutCoords("SVC-012", null);

        when(loadPublicServicePort.findAllByCoordXIsNullOrCoordYIsNull())
                .thenReturn(List.of(success, fail, skip));
        when(geocodingPort.geocode("경복궁")).thenReturn(Optional.of(coords(126.977, 37.579)));
        when(geocodingPort.geocode("없는장소")).thenReturn(Optional.empty());

        service.fillMissingCoords();

        verify(savePublicServicePort, times(1)).save(success);
        verify(savePublicServicePort, never()).save(fail);
        verify(savePublicServicePort, never()).save(skip);
    }
}
