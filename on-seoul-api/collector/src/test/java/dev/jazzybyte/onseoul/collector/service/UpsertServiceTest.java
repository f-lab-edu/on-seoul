package dev.jazzybyte.onseoul.collector.service;

import dev.jazzybyte.onseoul.collector.domain.ServiceChangeLog;
import dev.jazzybyte.onseoul.collector.dto.UpsertResult;
import dev.jazzybyte.onseoul.collector.enums.ChangeType;
import dev.jazzybyte.onseoul.collector.repository.ServiceChangeLogRepository;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.repository.PublicServiceReservationRepository;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class UpsertServiceTest {

    @Mock
    private PublicServiceReservationRepository reservationRepository;

    @Mock
    private ServiceChangeLogRepository changeLogRepository;

    @InjectMocks
    private UpsertService upsertService;

    @Test
    @DisplayName("DB에 없는 serviceId는 신규(NEW)로 INSERT된다")
    void new_entity_is_inserted() {
        PublicServiceReservation incoming = reservation("SVC001", "접수중",
                LocalDateTime.of(2026, 1, 1, 0, 0),
                LocalDateTime.of(2026, 3, 31, 23, 59));
        when(reservationRepository.findAllByServiceIdIn(anyCollection())).thenReturn(List.of());

        UpsertResult result = upsertService.upsert(List.of(incoming), 1L);

        verify(reservationRepository).save(incoming);
        assertThat(result.newCount()).isEqualTo(1);
        assertThat(result.updatedCount()).isEqualTo(0);
        assertThat(result.unchangedCount()).isEqualTo(0);
    }

    @Test
    @DisplayName("핵심 필드가 변경된 경우 UPDATE되고 ServiceChangeLog가 기록된다")
    void changed_entity_is_updated_with_change_log() {
        PublicServiceReservation existing = reservation("SVC001", "접수중",
                LocalDateTime.of(2026, 1, 1, 0, 0),
                LocalDateTime.of(2026, 3, 31, 23, 59));
        PublicServiceReservation incoming = reservation("SVC001", "안내중",  // status 변경
                LocalDateTime.of(2026, 1, 1, 0, 0),
                LocalDateTime.of(2026, 3, 31, 23, 59));
        when(reservationRepository.findAllByServiceIdIn(anyCollection())).thenReturn(List.of(existing));

        UpsertResult result = upsertService.upsert(List.of(incoming), 1L);

        verify(reservationRepository).save(existing);
        assertThat(result.updatedCount()).isEqualTo(1);

        ArgumentCaptor<List<ServiceChangeLog>> logCaptor = ArgumentCaptor.forClass(List.class);
        verify(changeLogRepository).saveAll(logCaptor.capture());
        List<ServiceChangeLog> logs = logCaptor.getValue();
        assertThat(logs).hasSize(1);
        assertThat(logs.get(0).getChangeType()).isEqualTo(ChangeType.UPDATED);
        assertThat(logs.get(0).getFieldName()).isEqualTo("serviceStatus");
        assertThat(logs.get(0).getOldValue()).isEqualTo("접수중");
        assertThat(logs.get(0).getNewValue()).isEqualTo("안내중");
    }

    @Test
    @DisplayName("UPDATE 시 prevServiceStatus에 변경 전 상태가 저장된다")
    void prev_service_status_is_saved_on_update() {
        PublicServiceReservation existing = reservation("SVC001", "접수중",
                LocalDateTime.of(2026, 1, 1, 0, 0),
                LocalDateTime.of(2026, 3, 31, 23, 59));
        PublicServiceReservation incoming = reservation("SVC001", "안내중",
                LocalDateTime.of(2026, 1, 1, 0, 0),
                LocalDateTime.of(2026, 3, 31, 23, 59));
        when(reservationRepository.findAllByServiceIdIn(anyCollection())).thenReturn(List.of(existing));

        upsertService.upsert(List.of(incoming), 1L);

        assertThat(existing.getPrevServiceStatus()).isEqualTo("접수중");
        assertThat(existing.getServiceStatus()).isEqualTo("안내중");
    }

    @Test
    @DisplayName("핵심 필드가 동일하면 UNCHANGED로 스킵된다")
    void unchanged_entity_is_skipped() {
        LocalDateTime start = LocalDateTime.of(2026, 1, 1, 0, 0);
        LocalDateTime end = LocalDateTime.of(2026, 3, 31, 23, 59);
        PublicServiceReservation existing = reservation("SVC001", "접수중", start, end);
        PublicServiceReservation incoming = reservation("SVC001", "접수중", start, end);
        when(reservationRepository.findAllByServiceIdIn(anyCollection())).thenReturn(List.of(existing));

        UpsertResult result = upsertService.upsert(List.of(incoming), 1L);

        verify(reservationRepository, never()).save(any());
        assertThat(result.unchangedCount()).isEqualTo(1);
    }

    @Test
    @DisplayName("입력이 비어있으면 모두 0을 반환하고 저장소를 호출하지 않는다")
    void empty_input_returns_zero_counts() {
        UpsertResult result = upsertService.upsert(List.of(), 1L);

        assertThat(result).isEqualTo(UpsertResult.empty());
        verifyNoInteractions(reservationRepository, changeLogRepository);
    }

    // ── helpers ──────────────────────────────────────────────────────────────

    private PublicServiceReservation reservation(String serviceId, String serviceStatus,
                                                  LocalDateTime receiptStart, LocalDateTime receiptEnd) {
        return PublicServiceReservation.builder()
                .serviceId(serviceId)
                .serviceName("테스트 서비스")
                .serviceStatus(serviceStatus)
                .receiptStartDt(receiptStart)
                .receiptEndDt(receiptEnd)
                .build();
    }
}
