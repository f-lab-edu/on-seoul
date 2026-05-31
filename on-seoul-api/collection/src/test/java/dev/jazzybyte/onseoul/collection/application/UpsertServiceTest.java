package dev.jazzybyte.onseoul.collection.application;

import dev.jazzybyte.onseoul.collection.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.collection.domain.ServiceChangeLog;
import dev.jazzybyte.onseoul.collection.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.collection.port.out.SavePublicServicePort;
import dev.jazzybyte.onseoul.collection.port.out.SaveServiceChangeLogPort;
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
import static org.assertj.core.api.Assertions.assertThatNullPointerException;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class UpsertServiceTest {

    @Mock private LoadPublicServicePort loadPublicServicePort;
    @Mock private SavePublicServicePort savePublicServicePort;
    @Mock private SaveServiceChangeLogPort saveServiceChangeLogPort;

    private UpsertService service;

    private static final Long COLLECTION_ID = 99L;

    @BeforeEach
    void setUp() {
        service = new UpsertService(loadPublicServicePort, savePublicServicePort, saveServiceChangeLogPort);
    }

    private PublicServiceReservation reservation(String serviceId, String status,
                                                 LocalDateTime receiptStart, LocalDateTime receiptEnd) {
        return PublicServiceReservation.builder()
                .serviceId(serviceId)
                .serviceName("서비스-" + serviceId)
                .serviceStatus(status)
                .receiptStartDt(receiptStart)
                .receiptEndDt(receiptEnd)
                .build();
    }

    private PublicServiceReservation reservation(String serviceId) {
        return reservation(serviceId, "접수중", now(), now().plusDays(7));
    }

    private LocalDateTime now() {
        return LocalDateTime.of(2025, 1, 1, 0, 0, 0);
    }

    @Test
    @DisplayName("incoming이 비어있으면 DB 조회 없이 UpsertResult.empty()를 반환한다")
    void upsert_emptyIncoming_returnsEmptyResultWithoutDbAccess() {
        UpsertService.UpsertResult result = service.upsert(List.of(), COLLECTION_ID);

        assertThat(result.newCount()).isZero();
        assertThat(result.updatedCount()).isZero();
        assertThat(result.unchangedCount()).isZero();
        verifyNoInteractions(loadPublicServicePort, savePublicServicePort, saveServiceChangeLogPort);
    }

    @Test
    @DisplayName("DB에 없는 항목들은 모두 신규로 분류되어 save()가 호출된다")
    void upsert_allNew_savesAllAndCountsNew() {
        List<PublicServiceReservation> incoming = List.of(
                reservation("SVC-001"), reservation("SVC-002"), reservation("SVC-003"));

        when(loadPublicServicePort.findAllByServiceIdIn(anyList())).thenReturn(List.of());

        UpsertService.UpsertResult result = service.upsert(incoming, COLLECTION_ID);

        assertThat(result.newCount()).isEqualTo(3);
        assertThat(result.updatedCount()).isZero();
        assertThat(result.unchangedCount()).isZero();
        verify(savePublicServicePort, times(3)).save(any());
        verify(saveServiceChangeLogPort, never()).saveAll(anyList());
    }

    @Test
    @DisplayName("핵심 필드가 동일한 항목들은 유지로 분류되어 save()가 호출되지 않는다")
    void upsert_allUnchanged_noSaveCallAndCountsUnchanged() {
        LocalDateTime start = now();
        LocalDateTime end = now().plusDays(7);
        PublicServiceReservation incoming = reservation("SVC-001", "접수중", start, end);
        PublicServiceReservation existing = reservation("SVC-001", "접수중", start, end);

        when(loadPublicServicePort.findAllByServiceIdIn(anyList())).thenReturn(List.of(existing));

        UpsertService.UpsertResult result = service.upsert(List.of(incoming), COLLECTION_ID);

        assertThat(result.unchangedCount()).isEqualTo(1);
        assertThat(result.newCount()).isZero();
        assertThat(result.updatedCount()).isZero();
        verify(savePublicServicePort, never()).save(any());
        verify(saveServiceChangeLogPort, never()).saveAll(anyList());
    }

    @Test
    @DisplayName("serviceStatus가 변경되면 updatedCount 증가, ChangeLog 1개 생성, save() 호출")
    void upsert_serviceStatusChanged_updatesAndCreatesChangeLog() {
        PublicServiceReservation incoming = reservation("SVC-001", "접수마감", now(), now().plusDays(7));
        PublicServiceReservation existing = reservation("SVC-001", "접수중", now(), now().plusDays(7));

        when(loadPublicServicePort.findAllByServiceIdIn(anyList())).thenReturn(List.of(existing));

        UpsertService.UpsertResult result = service.upsert(List.of(incoming), COLLECTION_ID);

        assertThat(result.updatedCount()).isEqualTo(1);
        assertThat(result.newCount()).isZero();
        verify(savePublicServicePort).save(existing);

        ArgumentCaptor<List<ServiceChangeLog>> logCaptor = ArgumentCaptor.forClass(List.class);
        verify(saveServiceChangeLogPort).saveAll(logCaptor.capture());
        List<ServiceChangeLog> logs = logCaptor.getValue();
        assertThat(logs).hasSize(1);
        assertThat(logs.get(0).getFieldName()).isEqualTo("serviceStatus");
        assertThat(logs.get(0).getOldValue()).isEqualTo("접수중");
        assertThat(logs.get(0).getNewValue()).isEqualTo("접수마감");
    }

    @Test
    @DisplayName("receiptStartDt가 변경되면 ChangeLog 1개 생성")
    void upsert_receiptStartDtChanged_createsChangeLog() {
        LocalDateTime oldStart = now();
        LocalDateTime newStart = now().plusDays(1);
        PublicServiceReservation incoming = reservation("SVC-002", "접수중", newStart, now().plusDays(7));
        PublicServiceReservation existing = reservation("SVC-002", "접수중", oldStart, now().plusDays(7));

        when(loadPublicServicePort.findAllByServiceIdIn(anyList())).thenReturn(List.of(existing));

        service.upsert(List.of(incoming), COLLECTION_ID);

        ArgumentCaptor<List<ServiceChangeLog>> logCaptor = ArgumentCaptor.forClass(List.class);
        verify(saveServiceChangeLogPort).saveAll(logCaptor.capture());
        assertThat(logCaptor.getValue()).hasSize(1);
        assertThat(logCaptor.getValue().get(0).getFieldName()).isEqualTo("receiptStartDt");
    }

    @Test
    @DisplayName("receiptEndDt가 변경되면 ChangeLog 1개 생성")
    void upsert_receiptEndDtChanged_createsChangeLog() {
        LocalDateTime oldEnd = now().plusDays(7);
        LocalDateTime newEnd = now().plusDays(14);
        PublicServiceReservation incoming = reservation("SVC-003", "접수중", now(), newEnd);
        PublicServiceReservation existing = reservation("SVC-003", "접수중", now(), oldEnd);

        when(loadPublicServicePort.findAllByServiceIdIn(anyList())).thenReturn(List.of(existing));

        service.upsert(List.of(incoming), COLLECTION_ID);

        ArgumentCaptor<List<ServiceChangeLog>> logCaptor = ArgumentCaptor.forClass(List.class);
        verify(saveServiceChangeLogPort).saveAll(logCaptor.capture());
        assertThat(logCaptor.getValue()).hasSize(1);
        assertThat(logCaptor.getValue().get(0).getFieldName()).isEqualTo("receiptEndDt");
    }

    @Test
    @DisplayName("핵심 필드 3개가 모두 변경되면 ChangeLog 3개 생성")
    void upsert_allCoreFieldsChanged_creates3ChangeLogs() {
        PublicServiceReservation incoming = reservation("SVC-004", "접수마감",
                now().plusDays(1), now().plusDays(14));
        PublicServiceReservation existing = reservation("SVC-004", "접수중",
                now(), now().plusDays(7));

        when(loadPublicServicePort.findAllByServiceIdIn(anyList())).thenReturn(List.of(existing));

        service.upsert(List.of(incoming), COLLECTION_ID);

        ArgumentCaptor<List<ServiceChangeLog>> logCaptor = ArgumentCaptor.forClass(List.class);
        verify(saveServiceChangeLogPort).saveAll(logCaptor.capture());
        assertThat(logCaptor.getValue()).hasSize(3);
        assertThat(logCaptor.getValue())
                .extracting(ServiceChangeLog::getFieldName)
                .containsExactlyInAnyOrder("serviceStatus", "receiptStartDt", "receiptEndDt");
    }

    @Test
    @DisplayName("변경 시 existing.prevServiceStatus가 기존 serviceStatus로 갱신된다")
    void upsert_statusChanged_prevServiceStatusUpdated() {
        PublicServiceReservation incoming = reservation("SVC-005", "접수마감", now(), now().plusDays(7));
        PublicServiceReservation existing = reservation("SVC-005", "접수중", now(), now().plusDays(7));

        when(loadPublicServicePort.findAllByServiceIdIn(anyList())).thenReturn(List.of(existing));

        service.upsert(List.of(incoming), COLLECTION_ID);

        assertThat(existing.getPrevServiceStatus()).isEqualTo("접수중");
        assertThat(existing.getServiceStatus()).isEqualTo("접수마감");
    }

    @Test
    @DisplayName("신규/변경/유지가 혼합된 경우 각 카운트가 정확하다")
    void upsert_mixed_countsAreAccurate() {
        LocalDateTime sameStart = now();
        LocalDateTime sameEnd = now().plusDays(7);

        PublicServiceReservation newItem = reservation("SVC-A", "접수중", sameStart, sameEnd);

        PublicServiceReservation updatedIncoming = reservation("SVC-B", "접수마감", sameStart, sameEnd);
        PublicServiceReservation updatedExisting = reservation("SVC-B", "접수중", sameStart, sameEnd);

        PublicServiceReservation unchangedItem = reservation("SVC-C", "접수중", sameStart, sameEnd);

        when(loadPublicServicePort.findAllByServiceIdIn(anyList()))
                .thenReturn(List.of(updatedExisting, unchangedItem));

        UpsertService.UpsertResult result = service.upsert(
                List.of(newItem, updatedIncoming, unchangedItem), COLLECTION_ID);

        assertThat(result.newCount()).isEqualTo(1);
        assertThat(result.updatedCount()).isEqualTo(1);
        assertThat(result.unchangedCount()).isEqualTo(1);
    }

    @Test
    @DisplayName("변경된 항목이 없으면 saveServiceChangeLogPort.saveAll()을 호출하지 않는다")
    void upsert_noChanges_changeLogSaveAllNotCalled() {
        PublicServiceReservation same = reservation("SVC-001", "접수중", now(), now().plusDays(7));

        when(loadPublicServicePort.findAllByServiceIdIn(anyList())).thenReturn(List.of(same));

        service.upsert(List.of(reservation("SVC-001", "접수중", now(), now().plusDays(7))), COLLECTION_ID);

        verify(saveServiceChangeLogPort, never()).saveAll(anyList());
    }

    @Test
    @DisplayName("ChangeLog의 collectionId는 upsert()에 전달된 collectionId와 일치한다")
    void upsert_changeLog_hasCorrectCollectionId() {
        PublicServiceReservation incoming = reservation("SVC-X", "접수마감", now(), now().plusDays(7));
        PublicServiceReservation existing = reservation("SVC-X", "접수중", now(), now().plusDays(7));

        when(loadPublicServicePort.findAllByServiceIdIn(anyList())).thenReturn(List.of(existing));

        service.upsert(List.of(incoming), 42L);

        ArgumentCaptor<List<ServiceChangeLog>> logCaptor = ArgumentCaptor.forClass(List.class);
        verify(saveServiceChangeLogPort).saveAll(logCaptor.capture());
        assertThat(logCaptor.getValue().get(0).getCollectionId()).isEqualTo(42L);
    }

    @Test
    @DisplayName("collectionId가 null이면 NOT NULL 제약 위반 전에 NPE로 빠르게 실패한다")
    void upsert_nullCollectionId_failsFast() {
        List<PublicServiceReservation> incoming = List.of(reservation("SVC-001"));

        assertThatNullPointerException()
                .isThrownBy(() -> service.upsert(incoming, null))
                .withMessageContaining("collectionId must not be null");

        // 가드가 DB 접근 이전에 동작해야 한다 (부분 저장 후 롤백 방지).
        verifyNoInteractions(loadPublicServicePort, savePublicServicePort, saveServiceChangeLogPort);
    }
}
