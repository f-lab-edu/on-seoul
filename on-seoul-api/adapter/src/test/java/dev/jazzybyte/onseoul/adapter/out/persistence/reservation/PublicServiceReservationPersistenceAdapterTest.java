package dev.jazzybyte.onseoul.adapter.out.persistence.reservation;

import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:testdb;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded"
})
@Import({PublicServiceReservationPersistenceAdapter.class, PublicServiceReservationPersistenceMapper.class})
class PublicServiceReservationPersistenceAdapterTest {

    @Autowired
    private PublicServiceReservationPersistenceAdapter adapter;

    private PublicServiceReservation buildReservation(String serviceId, BigDecimal coordX, BigDecimal coordY,
                                                       LocalDateTime deletedAt) {
        return PublicServiceReservation.builder()
                .serviceId(serviceId)
                .serviceGubun("문화행사")
                .maxClassName("문화행사")
                .minClassName("공연")
                .serviceName("서울 공공 문화행사 " + serviceId)
                .serviceStatus("접수중")
                .paymentType("무료")
                .lastSyncedAt(LocalDateTime.now())
                .coordX(coordX)
                .coordY(coordY)
                .deletedAt(deletedAt)
                .build();
    }

    // ── findAllByServiceIdIn ──────────────────────────────────────

    @Test
    @DisplayName("findAllByServiceIdIn() — 일치하는 serviceId만 반환")
    void findAllByServiceIdIn_returnsOnlyMatchingServiceIds() {
        adapter.save(buildReservation("SVC-001", null, null, null));
        adapter.save(buildReservation("SVC-002", null, null, null));
        adapter.save(buildReservation("SVC-003", null, null, null));

        List<PublicServiceReservation> result = adapter.findAllByServiceIdIn(List.of("SVC-001", "SVC-003"));

        assertThat(result).hasSize(2);
        assertThat(result).extracting(PublicServiceReservation::getServiceId)
                .containsExactlyInAnyOrder("SVC-001", "SVC-003");
    }

    // ── findAllByCoordXIsNullOrCoordYIsNull ───────────────────────

    @Test
    @DisplayName("findAllByCoordXIsNullOrCoordYIsNull() — 좌표 null 레코드만 반환")
    void findAllByCoordXIsNullOrCoordYIsNull_returnsNullCoordRecordsOnly() {
        adapter.save(buildReservation("SVC-010", null, null, null));                          // coordX=null
        adapter.save(buildReservation("SVC-011", new BigDecimal("126.9"), null, null));      // coordY=null
        adapter.save(buildReservation("SVC-012",
                new BigDecimal("126.9"), new BigDecimal("37.5"), null));                     // 좌표 있음

        List<PublicServiceReservation> result = adapter.findAllByCoordXIsNullOrCoordYIsNull();

        assertThat(result).hasSize(2);
        assertThat(result).extracting(PublicServiceReservation::getServiceId)
                .containsExactlyInAnyOrder("SVC-010", "SVC-011");
    }

    // ── findAllByDeletedAtIsNull ──────────────────────────────────

    @Test
    @DisplayName("findAllByDeletedAtIsNull() — soft-delete된 레코드 제외")
    void findAllByDeletedAtIsNull_excludesSoftDeleted() {
        adapter.save(buildReservation("SVC-020", null, null, null));                          // 정상
        adapter.save(buildReservation("SVC-021", null, null, LocalDateTime.now()));          // soft-deleted

        List<PublicServiceReservation> result = adapter.findAllByDeletedAtIsNull();

        assertThat(result).extracting(PublicServiceReservation::getServiceId)
                .contains("SVC-020")
                .doesNotContain("SVC-021");
    }

    // ── save (upsert by serviceId) ────────────────────────────────

    @Test
    @DisplayName("save() upsert — 동일 serviceId로 저장 시 기존 레코드가 업데이트된다")
    void save_upsertByServiceId_updatesExistingRecord() {
        adapter.save(buildReservation("SVC-030", null, null, null));

        PublicServiceReservation updated = PublicServiceReservation.builder()
                .serviceId("SVC-030")
                .serviceGubun("문화행사")
                .maxClassName("문화행사")
                .minClassName("공연")
                .serviceName("업데이트된 서비스명")
                .serviceStatus("마감")
                .prevServiceStatus("접수중")
                .paymentType("무료")
                .lastSyncedAt(LocalDateTime.now())
                .build();

        PublicServiceReservation saved = adapter.save(updated);

        assertThat(saved.getServiceId()).isEqualTo("SVC-030");
        assertThat(saved.getServiceName()).isEqualTo("업데이트된 서비스명");
        assertThat(saved.getServiceStatus()).isEqualTo("마감");

        // DB에 동일 serviceId 레코드가 하나만 존재해야 한다
        List<PublicServiceReservation> all = adapter.findAllByServiceIdIn(List.of("SVC-030"));
        assertThat(all).hasSize(1);
    }

    // ── saveAll ───────────────────────────────────────────────────

    @Test
    @DisplayName("saveAll() — 목록 저장")
    void saveAll_savesAllReservations() {
        List<PublicServiceReservation> reservations = List.of(
                buildReservation("SVC-040", null, null, null),
                buildReservation("SVC-041", new BigDecimal("126.9"), new BigDecimal("37.5"), null),
                buildReservation("SVC-042", null, null, null)
        );

        List<PublicServiceReservation> saved = adapter.saveAll(reservations);

        assertThat(saved).hasSize(3);
        assertThat(saved).extracting(PublicServiceReservation::getServiceId)
                .containsExactlyInAnyOrder("SVC-040", "SVC-041", "SVC-042");
        assertThat(saved).allMatch(r -> r.getId() != null);
    }
}
