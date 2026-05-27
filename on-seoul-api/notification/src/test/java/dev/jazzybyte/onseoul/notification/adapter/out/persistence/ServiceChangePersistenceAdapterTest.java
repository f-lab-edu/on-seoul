package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import org.jooq.DSLContext;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jooq.JooqTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Set;

import static dev.jazzybyte.onseoul.jooq.Tables.PUBLIC_SERVICE_RESERVATIONS;
import static dev.jazzybyte.onseoul.jooq.Tables.SERVICE_CHANGE_LOG;
import static org.assertj.core.api.Assertions.assertThat;

@JooqTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:notif-change-test;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.sql.init.mode=embedded",
        "spring.sql.init.schema-locations=classpath:jpa-test-schema.sql",
        "spring.jooq.sql-dialect=H2"
})
@Import(ServiceChangePersistenceAdapter.class)
class ServiceChangePersistenceAdapterTest {

    @Autowired private ServiceChangePersistenceAdapter adapter;
    @Autowired private DSLContext dsl;

    @BeforeEach
    void cleanup() {
        dsl.deleteFrom(SERVICE_CHANGE_LOG).execute();
        dsl.deleteFrom(PUBLIC_SERVICE_RESERVATIONS).execute();
    }

    private void insertReservation(String serviceId, String status, String areaName, String maxClassName) {
        insertReservation(serviceId, status, areaName, maxClassName, null);
    }

    private void insertReservation(String serviceId, String status, String areaName,
                                   String maxClassName, OffsetDateTime deletedAt) {
        dsl.insertInto(PUBLIC_SERVICE_RESERVATIONS)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_ID, serviceId)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_NAME, serviceId)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS, status)
                .set(PUBLIC_SERVICE_RESERVATIONS.AREA_NAME, areaName)
                .set(PUBLIC_SERVICE_RESERVATIONS.MAX_CLASS_NAME, maxClassName)
                .set(PUBLIC_SERVICE_RESERVATIONS.DELETED_AT, deletedAt)
                .execute();
    }

    private void insertChange(String serviceId, String changeType, String fieldName,
                              String oldValue, String newValue, OffsetDateTime changedAt) {
        dsl.insertInto(SERVICE_CHANGE_LOG)
                .set(SERVICE_CHANGE_LOG.SERVICE_ID, serviceId)
                .set(SERVICE_CHANGE_LOG.CHANGE_TYPE, changeType)
                .set(SERVICE_CHANGE_LOG.FIELD_NAME, fieldName)
                .set(SERVICE_CHANGE_LOG.OLD_VALUE, oldValue)
                .set(SERVICE_CHANGE_LOG.NEW_VALUE, newValue)
                .set(SERVICE_CHANGE_LOG.CHANGED_AT, changedAt)
                .execute();
    }

    /** UTC 기준 OffsetDateTime 편의 팩토리 */
    private static OffsetDateTime utc(int year, int month, int day, int hour, int minute) {
        return OffsetDateTime.of(year, month, day, hour, minute, 0, 0, ZoneOffset.UTC);
    }

    @Test
    @DisplayName("필터 비어 있으면 since=null 시 해당 serviceId의 전체 이력 반환")
    void loadFiltered_emptyFilter_returnsAll() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        insertReservation("OA-2266", "RECEIVING", "송파구", "체육시설");

        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
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
        OffsetDateTime base = utc(2026, 5, 1, 12, 0);
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", base);
        insertChange("OA-2269", "UPDATED", "service_name", "구", "신", base.plusHours(1));

        Instant since = base.toInstant();
        List<ServiceChange> result = adapter.loadFiltered("OA-2269", SubscriptionFilter.empty(), since);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).fieldName()).isEqualTo("service_name");
    }

    @Test
    @DisplayName("public_service_reservations에 매칭 row 없으면 빈 결과 (JOIN 실패)")
    void loadFiltered_noMatchingReservation_returnsEmpty() {
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        List<ServiceChange> result = adapter.loadFiltered("OA-2269", SubscriptionFilter.empty(), null);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("deleted_at이 NULL이 아니면 결과에서 제외된다")
    void loadFiltered_softDeletedReservation_excluded() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사", OffsetDateTime.now(ZoneOffset.UTC));
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        List<ServiceChange> result = adapter.loadFiltered("OA-2269", SubscriptionFilter.empty(), null);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("statuses 필터 — service_status가 IN 절에 포함되는 row만 반환")
    void loadFiltered_statusesFilter_filtersOut() {
        insertReservation("OA-2269", "CLOSED", "강남구", "문화행사");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        // CLOSED는 필터(RECEIVING)에 매칭 안됨
        List<ServiceChange> filtered = adapter.loadFiltered(
                "OA-2269",
                new SubscriptionFilter(Set.of("RECEIVING"), Set.of(), Set.of()),
                null);
        assertThat(filtered).isEmpty();

        // RECEIVING으로 변경하면 매칭
        dsl.update(PUBLIC_SERVICE_RESERVATIONS)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS, "RECEIVING")
                .where(PUBLIC_SERVICE_RESERVATIONS.SERVICE_ID.eq("OA-2269"))
                .execute();
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
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
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
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
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
        OffsetDateTime base = utc(2026, 5, 1, 10, 0);
        insertChange("OA-2269", "UPDATED", "f1", null, null, base.plusHours(2));
        insertChange("OA-2269", "UPDATED", "f2", null, null, base.plusHours(1));
        insertChange("OA-2269", "UPDATED", "f3", null, null, base);

        List<ServiceChange> result = adapter.loadFiltered("OA-2269", SubscriptionFilter.empty(), null);

        assertThat(result).extracting(ServiceChange::fieldName)
                .containsExactly("f3", "f2", "f1");
    }

    @Test
    @DisplayName("changedAt은 TIMESTAMPTZ에서 Instant으로 정확히 변환된다")
    void loadFiltered_changedAtConvertedToInstant() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        OffsetDateTime insertedAt = utc(2026, 5, 1, 12, 0);
        insertChange("OA-2269", "NEW", "service_status", null, "RECEIVING", insertedAt);

        List<ServiceChange> result = adapter.loadFiltered("OA-2269", SubscriptionFilter.empty(), null);

        assertThat(result.get(0).changedAt()).isEqualTo(insertedAt.toInstant());
    }
}
