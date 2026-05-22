package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.test.context.TestPropertySource;

import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

@JdbcTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:notif-change-test;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.sql.init.mode=embedded",
        "spring.sql.init.schema-locations=classpath:jpa-test-schema.sql"
})
@Import(ServiceChangePersistenceAdapter.class)
class ServiceChangePersistenceAdapterTest {

    @Autowired private ServiceChangePersistenceAdapter adapter;
    @Autowired private NamedParameterJdbcTemplate jdbc;

    private static final ZoneId SEOUL = ZoneId.of("Asia/Seoul");

    @BeforeEach
    void cleanup() {
        jdbc.update("DELETE FROM service_change_log", Map.of());
        jdbc.update("DELETE FROM public_service_reservations", Map.of());
    }

    private void insertReservation(String serviceId, String status, String areaName, String maxClassName) {
        insertReservation(serviceId, status, areaName, maxClassName, null);
    }

    private void insertReservation(String serviceId, String status, String areaName,
                                   String maxClassName, LocalDateTime deletedAt) {
        Map<String, Object> params = new HashMap<>();
        params.put("serviceId", serviceId);
        params.put("status", status);
        params.put("areaName", areaName);
        params.put("maxClassName", maxClassName);
        params.put("deletedAt", deletedAt == null ? null : Timestamp.valueOf(deletedAt));
        jdbc.update(
                "INSERT INTO public_service_reservations (service_id, service_name, service_status, area_name, max_class_name, deleted_at) " +
                        "VALUES (:serviceId, :serviceId, :status, :areaName, :maxClassName, :deletedAt)",
                params);
    }

    private void insertChange(String serviceId, String changeType, String fieldName,
                              String oldValue, String newValue, LocalDateTime changedAt) {
        Map<String, Object> params = new HashMap<>();
        params.put("serviceId", serviceId);
        params.put("changeType", changeType);
        params.put("fieldName", fieldName);
        params.put("oldValue", oldValue);
        params.put("newValue", newValue);
        params.put("changedAt", Timestamp.valueOf(changedAt));
        jdbc.update(
                "INSERT INTO service_change_log (service_id, change_type, field_name, old_value, new_value, changed_at) " +
                        "VALUES (:serviceId, :changeType, :fieldName, :oldValue, :newValue, :changedAt)",
                params);
    }

    @Test
    @DisplayName("필터 비어 있으면 since=null 시 해당 serviceId의 전체 이력 반환")
    void loadFiltered_emptyFilter_returnsAll() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        insertReservation("OA-2266", "RECEIVING", "송파구", "체육시설");

        LocalDateTime now = LocalDateTime.now();
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now.minusHours(2));
        insertChange("OA-2269", "UPDATED", "service_name", "구", "신", now.minusHours(1));
        insertChange("OA-2266", "NEW", null, null, null, now);

        List<ServiceChange> result = adapter.loadFiltered("OA-2269", SubscriptionFilter.empty(), null);

        assertThat(result).hasSize(2);
        assertThat(result).allMatch(c -> c.serviceId().equals("OA-2269"));
    }

    @Test
    @DisplayName("since 이후(exclusive) 데이터만 반환")
    void loadFiltered_withSince_returnsOnlyAfter() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        LocalDateTime base = LocalDateTime.of(2026, 5, 1, 12, 0, 0);
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", base);
        insertChange("OA-2269", "UPDATED", "service_name", "구", "신", base.plusHours(1));

        Instant since = base.atZone(SEOUL).toInstant();
        List<ServiceChange> result = adapter.loadFiltered("OA-2269", SubscriptionFilter.empty(), since);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).fieldName()).isEqualTo("service_name");
    }

    @Test
    @DisplayName("public_service_reservations에 매칭 row 없으면 빈 결과 (JOIN 실패)")
    void loadFiltered_noMatchingReservation_returnsEmpty() {
        // 예약 row 미삽입 — JOIN 실패
        LocalDateTime now = LocalDateTime.now();
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        List<ServiceChange> result = adapter.loadFiltered("OA-2269", SubscriptionFilter.empty(), null);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("deleted_at이 NULL이 아니면 결과에서 제외된다")
    void loadFiltered_softDeletedReservation_excluded() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사", LocalDateTime.now());
        LocalDateTime now = LocalDateTime.now();
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        List<ServiceChange> result = adapter.loadFiltered("OA-2269", SubscriptionFilter.empty(), null);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("statuses 필터 — service_status가 IN 절에 포함되는 row만 반환")
    void loadFiltered_statusesFilter_filtersOut() {
        insertReservation("OA-2269", "CLOSED", "강남구", "문화행사");
        LocalDateTime now = LocalDateTime.now();
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        // CLOSED는 필터(RECEIVING)에 매칭 안됨
        List<ServiceChange> filtered = adapter.loadFiltered(
                "OA-2269",
                new SubscriptionFilter(Set.of("RECEIVING"), Set.of(), Set.of()),
                null);
        assertThat(filtered).isEmpty();

        // RECEIVING으로 변경하면 매칭
        jdbc.update("UPDATE public_service_reservations SET service_status='RECEIVING' WHERE service_id='OA-2269'",
                Map.of());
        List<ServiceChange> matched = adapter.loadFiltered(
                "OA-2269",
                new SubscriptionFilter(Set.of("RECEIVING"), Set.of(), Set.of()),
                null);
        assertThat(matched).hasSize(1);
    }

    @Test
    @DisplayName("areaNames 필터 — area_name이 매칭하지 않으면 제외")
    void loadFiltered_areaNamesFilter_filtersOut() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        LocalDateTime now = LocalDateTime.now();
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        List<ServiceChange> miss = adapter.loadFiltered(
                "OA-2269",
                new SubscriptionFilter(Set.of(), Set.of("송파구"), Set.of()),
                null);
        assertThat(miss).isEmpty();

        List<ServiceChange> hit = adapter.loadFiltered(
                "OA-2269",
                new SubscriptionFilter(Set.of(), Set.of("강남구"), Set.of()),
                null);
        assertThat(hit).hasSize(1);
    }

    @Test
    @DisplayName("maxClassNames 필터 — max_class_name이 매칭하지 않으면 제외")
    void loadFiltered_maxClassNamesFilter_filtersOut() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        LocalDateTime now = LocalDateTime.now();
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        List<ServiceChange> miss = adapter.loadFiltered(
                "OA-2269",
                new SubscriptionFilter(Set.of(), Set.of(), Set.of("체육시설")),
                null);
        assertThat(miss).isEmpty();

        List<ServiceChange> hit = adapter.loadFiltered(
                "OA-2269",
                new SubscriptionFilter(Set.of(), Set.of(), Set.of("문화행사")),
                null);
        assertThat(hit).hasSize(1);
    }

    @Test
    @DisplayName("결과는 changed_at ASC 순으로 정렬된다")
    void loadFiltered_orderedByChangedAtAsc() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        LocalDateTime base = LocalDateTime.of(2026, 5, 1, 10, 0, 0);
        insertChange("OA-2269", "UPDATED", "f1", null, null, base.plusHours(2));
        insertChange("OA-2269", "UPDATED", "f2", null, null, base.plusHours(1));
        insertChange("OA-2269", "UPDATED", "f3", null, null, base);

        List<ServiceChange> result = adapter.loadFiltered("OA-2269", SubscriptionFilter.empty(), null);

        assertThat(result).extracting(ServiceChange::fieldName)
                .containsExactly("f3", "f2", "f1");
    }

    @Test
    @DisplayName("changedAt은 Asia/Seoul 기준으로 변환된 Instant이다")
    void loadFiltered_changedAtConvertedFromSeoulTimezone() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        LocalDateTime ldt = LocalDateTime.of(2026, 5, 1, 12, 0, 0);
        insertChange("OA-2269", "NEW", "service_status", null, "RECEIVING", ldt);

        List<ServiceChange> result = adapter.loadFiltered("OA-2269", SubscriptionFilter.empty(), null);

        Instant expected = ldt.atZone(SEOUL).toInstant();
        assertThat(result.get(0).changedAt()).isEqualTo(expected);
    }
}
