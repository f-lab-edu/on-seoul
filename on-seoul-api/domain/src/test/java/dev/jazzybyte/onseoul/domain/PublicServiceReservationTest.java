package dev.jazzybyte.onseoul.domain;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;

import java.time.temporal.ChronoUnit;

class PublicServiceReservationTest {

    private PublicServiceReservation original;

    @BeforeEach
    void setUp() {
        original = PublicServiceReservation.builder()
                .serviceId("SVC-001")
                .serviceName("수영장 강습")
                .serviceStatus("접수중")
                .placeName("잠실 체육관")
                .receiptStartDt(LocalDateTime.of(2026, 4, 1, 9, 0))
                .receiptEndDt(LocalDateTime.of(2026, 4, 30, 18, 0))
                .serviceUrl("https://example.com/svc/001")
                .coordX(new BigDecimal("127.123456789012345"))
                .coordY(new BigDecimal("37.123456789012345"))
                .build();
    }

    @Nested
    @DisplayName("update()")
    class Update {

        @Test
        @DisplayName("변경 전 serviceStatus가 prevServiceStatus로 이동한다")
        void prevServiceStatus_is_set_to_previous_value() {
            PublicServiceReservation updated = PublicServiceReservation.builder()
                    .serviceId("SVC-001")
                    .serviceName("수영장 강습")
                    .serviceStatus("마감")
                    .build();

            original.update(updated);

            assertThat(original.getPrevServiceStatus()).isEqualTo("접수중");
        }

        @Test
        @DisplayName("serviceStatus가 새 값으로 교체된다")
        void serviceStatus_is_replaced_with_new_value() {
            PublicServiceReservation updated = PublicServiceReservation.builder()
                    .serviceId("SVC-001")
                    .serviceName("수영장 강습")
                    .serviceStatus("마감")
                    .build();

            original.update(updated);

            assertThat(original.getServiceStatus()).isEqualTo("마감");
        }

        @Test
        @DisplayName("추적 대상 필드(serviceName, placeName, 접수기간, URL, 좌표)가 모두 갱신된다")
        void tracked_fields_are_all_updated() {
            LocalDateTime newReceiptStart = LocalDateTime.of(2026, 5, 1, 9, 0);
            LocalDateTime newReceiptEnd = LocalDateTime.of(2026, 5, 31, 18, 0);

            PublicServiceReservation updated = PublicServiceReservation.builder()
                    .serviceId("SVC-001")
                    .serviceName("수영장 강습 (개정)")
                    .serviceStatus("접수중")
                    .placeName("송파 체육관")
                    .receiptStartDt(newReceiptStart)
                    .receiptEndDt(newReceiptEnd)
                    .serviceUrl("https://example.com/svc/001/new")
                    .coordX(new BigDecimal("127.999999999999999"))
                    .coordY(new BigDecimal("37.999999999999999"))
                    .build();

            original.update(updated);

            assertThat(original.getServiceName()).isEqualTo("수영장 강습 (개정)");
            assertThat(original.getPlaceName()).isEqualTo("송파 체육관");
            assertThat(original.getReceiptStartDt()).isEqualTo(newReceiptStart);
            assertThat(original.getReceiptEndDt()).isEqualTo(newReceiptEnd);
            assertThat(original.getServiceUrl()).isEqualTo("https://example.com/svc/001/new");
            assertThat(original.getCoordX()).isEqualByComparingTo(new BigDecimal("127.999999999999999"));
            assertThat(original.getCoordY()).isEqualByComparingTo(new BigDecimal("37.999999999999999"));
        }

        @Test
        @DisplayName("lastSyncedAt이 현재 시각으로 갱신된다")
        void lastSyncedAt_is_updated_to_now() {
            LocalDateTime before = LocalDateTime.now();

            PublicServiceReservation updated = PublicServiceReservation.builder()
                    .serviceId("SVC-001")
                    .serviceName("수영장 강습")
                    .serviceStatus("마감")
                    .build();

            original.update(updated);

            assertThat(original.getLastSyncedAt())
                    .isAfterOrEqualTo(before)
                    .isCloseTo(LocalDateTime.now(), within(1, ChronoUnit.SECONDS));
        }
    }

    @Nested
    @DisplayName("softDelete()")
    class SoftDelete {

        @Test
        @DisplayName("deletedAt이 현재 시각으로 설정된다")
        void deletedAt_is_set_to_now() {
            LocalDateTime before = LocalDateTime.now();

            original.softDelete();

            assertThat(original.getDeletedAt())
                    .isNotNull()
                    .isAfterOrEqualTo(before)
                    .isCloseTo(LocalDateTime.now(), within(1, ChronoUnit.SECONDS));
        }

        @Test
        @DisplayName("softDelete 전에는 deletedAt이 null이다")
        void deletedAt_is_null_before_soft_delete() {
            assertThat(original.getDeletedAt()).isNull();
        }
    }
}
