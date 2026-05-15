package dev.jazzybyte.onseoul.collection.application;

import dev.jazzybyte.onseoul.collection.application.GeocodingService;
import dev.jazzybyte.onseoul.collection.domain.ApiSourceCatalog;
import dev.jazzybyte.onseoul.collection.domain.CollectionHistory;
import dev.jazzybyte.onseoul.collection.domain.CollectionStatus;
import dev.jazzybyte.onseoul.collection.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.collection.port.out.LoadApiSourceCatalogPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.collection.port.out.SaveCollectionHistoryPort;
import dev.jazzybyte.onseoul.collection.port.out.SavePublicServicePort;
import dev.jazzybyte.onseoul.collection.port.out.SeoulDatasetFetchPort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class CollectDatasetServiceTest {

    @Mock private LoadApiSourceCatalogPort catalogPort;
    @Mock private SaveCollectionHistoryPort historyPort;
    @Mock private LoadPublicServicePort loadPublicServicePort;
    @Mock private SavePublicServicePort savePublicServicePort;
    @Mock private SeoulDatasetFetchPort fetchPort;
    @Mock private UpsertService upsertService;
    @Mock private GeocodingService geocodingService;

    private CollectDatasetService service;

    @BeforeEach
    void setUp() {
        service = new CollectDatasetService(
                catalogPort, historyPort, loadPublicServicePort, savePublicServicePort,
                fetchPort, upsertService, geocodingService);
    }

    private ApiSourceCatalog source(long id, String datasetId, String path) {
        return ApiSourceCatalog.builder()
                .id(id)
                .datasetId(datasetId)
                .datasetName("테스트 데이터셋-" + datasetId)
                .apiServicePath(path)
                .active(true)
                .build();
    }

    private PublicServiceReservation reservation(String serviceId) {
        return PublicServiceReservation.builder()
                .serviceId(serviceId)
                .serviceName("서비스-" + serviceId)
                .serviceStatus("접수중")
                .firstCollectedAt(LocalDateTime.now())
                .build();
    }

    private UpsertService.UpsertResult emptyResult() {
        return new UpsertService.UpsertResult(0, 0, 0);
    }

    @Test
    @DisplayName("활성 소스가 없으면 fetchPort를 호출하지 않고 조기 반환한다")
    void collectAll_noActiveSources_skipsCollection() {
        when(catalogPort.findAllByActiveTrue()).thenReturn(List.of());

        service.collectAll();

        verifyNoInteractions(fetchPort, historyPort, upsertService, geocodingService);
    }

    @Test
    @DisplayName("단일 소스 성공 시 history를 2회 저장하고 geocodingService를 호출한다")
    void collectAll_singleSourceSuccess_savesHistoryTwiceAndCallsGeocoding() {
        ApiSourceCatalog src = source(1L, "OA-2266", "/ListPublicReservationSports");
        when(catalogPort.findAllByActiveTrue()).thenReturn(List.of(src));
        when(fetchPort.fetchAll("/ListPublicReservationSports"))
                .thenReturn(List.of(reservation("SVC-001"), reservation("SVC-002")));
        when(upsertService.upsert(anyList(), any())).thenReturn(emptyResult());
        when(loadPublicServicePort.findAllByDeletedAtIsNull()).thenReturn(List.of());

        service.collectAll();

        verify(historyPort, times(2)).save(any(CollectionHistory.class));
        verify(geocodingService).fillMissingCoords();
    }

    @Test
    @DisplayName("단일 소스 성공 시 history의 최종 상태는 SUCCESS다")
    void collectAll_singleSourceSuccess_historyStatusIsSuccess() {
        ApiSourceCatalog src = source(1L, "OA-2266", "/path");
        when(catalogPort.findAllByActiveTrue()).thenReturn(List.of(src));
        when(fetchPort.fetchAll(anyString())).thenReturn(List.of(reservation("SVC-001")));
        when(upsertService.upsert(anyList(), any())).thenReturn(emptyResult());
        when(loadPublicServicePort.findAllByDeletedAtIsNull()).thenReturn(List.of());

        service.collectAll();

        ArgumentCaptor<CollectionHistory> historyCaptor = ArgumentCaptor.forClass(CollectionHistory.class);
        verify(historyPort, times(2)).save(historyCaptor.capture());

        CollectionHistory finalHistory = historyCaptor.getAllValues().get(1);
        assertThat(finalHistory.getStatus()).isEqualTo(CollectionStatus.SUCCESS);
    }

    @Test
    @DisplayName("fetchAll에서 예외 발생 시 history를 FAILED로 저장하고 예외를 전파하지 않는다")
    void collectAll_fetchAllThrows_historyFailedAndNoExceptionPropagation() {
        ApiSourceCatalog src = source(1L, "OA-2266", "/path");
        when(catalogPort.findAllByActiveTrue()).thenReturn(List.of(src));
        when(fetchPort.fetchAll(anyString())).thenThrow(new RuntimeException("서울시 API 응답 없음"));

        service.collectAll();

        ArgumentCaptor<CollectionHistory> historyCaptor = ArgumentCaptor.forClass(CollectionHistory.class);
        verify(historyPort, times(2)).save(historyCaptor.capture());
        CollectionHistory failHistory = historyCaptor.getAllValues().get(1);
        assertThat(failHistory.getStatus()).isEqualTo(CollectionStatus.FAILED);
        assertThat(failHistory.getErrorMessage()).contains("서울시 API 응답 없음");
    }

    @Test
    @DisplayName("일부 소스 실패 시 나머지 소스는 계속 수집하고 deletion sweep을 건너뜀")
    void collectAll_partialFailure_continuesAndSkipsDeletionSweep() {
        ApiSourceCatalog src1 = source(1L, "OA-2266", "/path1");
        ApiSourceCatalog src2 = source(2L, "OA-2267", "/path2");
        ApiSourceCatalog src3 = source(3L, "OA-2268", "/path3");

        when(catalogPort.findAllByActiveTrue()).thenReturn(List.of(src1, src2, src3));
        when(fetchPort.fetchAll("/path1")).thenReturn(List.of(reservation("SVC-A")));
        when(fetchPort.fetchAll("/path2")).thenThrow(new RuntimeException("타임아웃"));
        when(fetchPort.fetchAll("/path3")).thenReturn(List.of(reservation("SVC-C")));
        when(upsertService.upsert(anyList(), any())).thenReturn(emptyResult());

        service.collectAll();

        verify(fetchPort).fetchAll("/path1");
        verify(fetchPort).fetchAll("/path2");
        verify(fetchPort).fetchAll("/path3");

        verify(loadPublicServicePort, never()).findAllByDeletedAtIsNull();

        verify(geocodingService).fillMissingCoords();
    }

    @Test
    @DisplayName("모든 소스 성공 시 deletion sweep을 실행한다")
    void collectAll_allSuccess_runsDeletionSweep() {
        ApiSourceCatalog src = source(1L, "OA-2266", "/path");
        when(catalogPort.findAllByActiveTrue()).thenReturn(List.of(src));
        when(fetchPort.fetchAll(anyString())).thenReturn(List.of(reservation("SVC-001")));
        when(upsertService.upsert(anyList(), any())).thenReturn(emptyResult());
        when(loadPublicServicePort.findAllByDeletedAtIsNull()).thenReturn(List.of());

        service.collectAll();

        verify(loadPublicServicePort).findAllByDeletedAtIsNull();
    }

    @Test
    @DisplayName("deletion sweep — seen 목록에 없는 레코드를 soft-delete하고 saveAll()을 호출한다")
    void collectAll_deletionSweep_softDeletesUnseenRecords() {
        ApiSourceCatalog src = source(1L, "OA-2266", "/path");
        PublicServiceReservation collected = reservation("SVC-SEEN");
        PublicServiceReservation orphan = reservation("SVC-ORPHAN");

        when(catalogPort.findAllByActiveTrue()).thenReturn(List.of(src));
        when(fetchPort.fetchAll(anyString())).thenReturn(List.of(collected));
        when(upsertService.upsert(anyList(), any())).thenReturn(emptyResult());
        when(loadPublicServicePort.findAllByDeletedAtIsNull()).thenReturn(List.of(collected, orphan));

        service.collectAll();

        ArgumentCaptor<List<PublicServiceReservation>> captor = ArgumentCaptor.forClass(List.class);
        verify(savePublicServicePort).saveAll(captor.capture());
        List<PublicServiceReservation> deleted = captor.getValue();
        assertThat(deleted).hasSize(1);
        assertThat(deleted.get(0).getServiceId()).isEqualTo("SVC-ORPHAN");
        assertThat(deleted.get(0).getDeletedAt()).isNotNull();
    }

    @Test
    @DisplayName("deletion sweep — 삭제 대상이 없으면 saveAll()을 호출하지 않는다")
    void collectAll_deletionSweep_noDeletableRecords_doesNotCallSaveAll() {
        ApiSourceCatalog src = source(1L, "OA-2266", "/path");
        PublicServiceReservation collected = reservation("SVC-001");

        when(catalogPort.findAllByActiveTrue()).thenReturn(List.of(src));
        when(fetchPort.fetchAll(anyString())).thenReturn(List.of(collected));
        when(upsertService.upsert(anyList(), any())).thenReturn(emptyResult());
        when(loadPublicServicePort.findAllByDeletedAtIsNull()).thenReturn(List.of(collected));

        service.collectAll();

        verify(savePublicServicePort, never()).saveAll(anyList());
    }
}
