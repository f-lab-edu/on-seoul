package dev.jazzybyte.onseoul.collector.service;

import dev.jazzybyte.onseoul.collector.PublicServiceRowMapper;
import dev.jazzybyte.onseoul.collector.SeoulOpenApiClient;
import dev.jazzybyte.onseoul.collector.domain.ApiSourceCatalog;
import dev.jazzybyte.onseoul.collector.domain.CollectionHistory;
import dev.jazzybyte.onseoul.collector.dto.PublicServiceRow;
import dev.jazzybyte.onseoul.collector.dto.UpsertResult;
import dev.jazzybyte.onseoul.collector.enums.CollectionStatus;
import dev.jazzybyte.onseoul.collector.exception.SeoulApiException;
import dev.jazzybyte.onseoul.collector.repository.ApiSourceCatalogRepository;
import dev.jazzybyte.onseoul.collector.repository.CollectionHistoryRepository;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.repository.PublicServiceReservationRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class CollectionServiceTest {

    @Mock private ApiSourceCatalogRepository catalogRepository;
    @Mock private CollectionHistoryRepository historyRepository;
    @Mock private PublicServiceReservationRepository reservationRepository;
    @Mock private SeoulOpenApiClient apiClient;
    @Mock private PublicServiceRowMapper rowMapper;
    @Mock private UpsertService upsertService;
    @Mock private GeocodingService geocodingService;

    @InjectMocks
    private CollectionService collectionService;

    private ApiSourceCatalog source1;
    private ApiSourceCatalog source2;

    @BeforeEach
    void setUp() {
        source1 = ApiSourceCatalog.builder()
                .datasetId("OA-2266").datasetName("체육시설")
                .datasetUrl("http://example.com").apiServicePath("ListPublicReservationSports")
                .active(true).build();
        source2 = ApiSourceCatalog.builder()
                .datasetId("OA-2267").datasetName("시설대관")
                .datasetUrl("http://example.com").apiServicePath("ListPublicReservationInstitution")
                .active(true).build();

        when(historyRepository.save(any())).thenAnswer(inv -> inv.getArgument(0));
        lenient().doNothing().when(geocodingService).fillMissingCoords();
    }

    @Test
    @DisplayName("모든 소스 수집 성공 시 각 CollectionHistory가 SUCCESS로 기록된다")
    void all_sources_succeed() {
        when(catalogRepository.findAllByActiveTrue()).thenReturn(List.of(source1, source2));

        PublicServiceRow row = new PublicServiceRow();
        when(apiClient.fetchAll(anyString())).thenReturn(List.of(row));

        PublicServiceReservation entity = reservation("SVC001");
        when(rowMapper.toEntity(row)).thenReturn(Optional.of(entity));
        when(upsertService.upsert(anyList(), any())).thenReturn(new UpsertResult(1, 0, 0));
        when(reservationRepository.findAllByDeletedAtIsNull()).thenReturn(List.of(entity));

        collectionService.collectAll();

        // 소스당 2번 save (생성 시 FAILED 초기상태, 완료 시 SUCCESS)
        ArgumentCaptor<CollectionHistory> captor = ArgumentCaptor.forClass(CollectionHistory.class);
        verify(historyRepository, times(4)).save(captor.capture());

        // 완료된 이력(durationMs != null)의 상태는 모두 SUCCESS
        captor.getAllValues().stream()
                .filter(h -> h.getDurationMs() != null)
                .forEach(h -> assertThat(h.getStatus()).isEqualTo(CollectionStatus.SUCCESS));
    }

    @Test
    @DisplayName("한 소스 실패 시 FAILED 이력을 기록하고 나머지 소스는 계속 수집된다")
    void one_source_fails_others_continue() {
        when(catalogRepository.findAllByActiveTrue()).thenReturn(List.of(source1, source2));
        when(apiClient.fetchAll("ListPublicReservationSports"))
                .thenThrow(new SeoulApiException(ErrorCode.COLLECT_API_SERVER_ERROR, "서버 오류"));

        PublicServiceRow row = new PublicServiceRow();
        when(apiClient.fetchAll("ListPublicReservationInstitution")).thenReturn(List.of(row));
        PublicServiceReservation entity = reservation("SVC002");
        when(rowMapper.toEntity(row)).thenReturn(Optional.of(entity));
        when(upsertService.upsert(anyList(), any())).thenReturn(new UpsertResult(1, 0, 0));

        collectionService.collectAll();

        // source2는 정상 수집됨
        verify(upsertService, times(1)).upsert(anyList(), any());
        // 부분 실패 시 deletion sweep 스킵
        verify(reservationRepository, never()).findAllByDeletedAtIsNull();
    }

    @Test
    @DisplayName("전체 성공 후 수집에 포함되지 않은 DB 레코드는 soft-delete된다")
    void deletion_sweep_soft_deletes_stale_records() {
        when(catalogRepository.findAllByActiveTrue()).thenReturn(List.of(source1));

        PublicServiceRow row = new PublicServiceRow();
        when(apiClient.fetchAll(anyString())).thenReturn(List.of(row));
        PublicServiceReservation collected = reservation("SVC001");
        PublicServiceReservation stale = reservation("SVC_STALE");
        when(rowMapper.toEntity(row)).thenReturn(Optional.of(collected));
        when(upsertService.upsert(anyList(), any())).thenReturn(new UpsertResult(0, 0, 1));
        when(reservationRepository.findAllByDeletedAtIsNull()).thenReturn(List.of(collected, stale));

        collectionService.collectAll();

        // stale 레코드만 soft-delete → saveAll로 저장
        ArgumentCaptor<List<PublicServiceReservation>> saveCaptor = ArgumentCaptor.forClass(List.class);
        verify(reservationRepository).saveAll(saveCaptor.capture());
        assertThat(saveCaptor.getValue()).containsExactly(stale);
        assertThat(stale.getDeletedAt()).isNotNull();
    }

    @Test
    @DisplayName("활성 소스가 없으면 아무 작업도 수행하지 않는다")
    void no_active_sources_does_nothing() {
        when(catalogRepository.findAllByActiveTrue()).thenReturn(List.of());

        collectionService.collectAll();

        verifyNoInteractions(apiClient, upsertService, reservationRepository);
    }

    // ── helpers ──────────────────────────────────────────────────────────────

    private PublicServiceReservation reservation(String serviceId) {
        return PublicServiceReservation.builder()
                .serviceId(serviceId)
                .serviceName("테스트 서비스")
                .serviceStatus("접수중")
                .build();
    }
}
