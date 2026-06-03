package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.KeywordTarget;
import dev.jazzybyte.onseoul.notification.domain.ScheduledServiceMatch;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import org.jooq.DSLContext;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jooq.JooqTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Set;

import static dev.jazzybyte.onseoul.jooq.Tables.PUBLIC_SERVICE_RESERVATIONS;
import static org.assertj.core.api.Assertions.assertThat;

@JooqTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:notif-scheduled-test;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.sql.init.mode=embedded",
        "spring.sql.init.schema-locations=classpath:jpa-test-schema.sql",
        "spring.jooq.sql-dialect=H2"
})
@Import(ScheduledTriggerPersistenceAdapter.class)
class ScheduledTriggerPersistenceAdapterTest {

    @Autowired private ScheduledTriggerPersistenceAdapter adapter;
    @Autowired private DSLContext dsl;

    private static final LocalDate TODAY = LocalDate.of(2026, 6, 3);

    @BeforeEach
    void cleanup() {
        dsl.deleteFrom(PUBLIC_SERVICE_RESERVATIONS).execute();
    }

    /** day 의 정오(UTC) — [day, day+1) 구간 내부. */
    private static OffsetDateTime noon(LocalDate day) {
        return day.atTime(12, 0).atOffset(ZoneOffset.UTC);
    }

    private void insert(String serviceId, String status, String area, String maxClass,
                        OffsetDateTime openStart, OffsetDateTime receiptStart, OffsetDateTime receiptEnd,
                        OffsetDateTime deletedAt) {
        dsl.insertInto(PUBLIC_SERVICE_RESERVATIONS)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_ID, serviceId)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_NAME, serviceId + "-name")
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS, status)
                .set(PUBLIC_SERVICE_RESERVATIONS.AREA_NAME, area)
                .set(PUBLIC_SERVICE_RESERVATIONS.MAX_CLASS_NAME, maxClass)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_OPEN_START_DT, openStart)
                .set(PUBLIC_SERVICE_RESERVATIONS.RECEIPT_START_DT, receiptStart)
                .set(PUBLIC_SERVICE_RESERVATIONS.RECEIPT_END_DT, receiptEnd)
                .set(PUBLIC_SERVICE_RESERVATIONS.DELETED_AT, deletedAt)
                .execute();
    }

    // ── OPEN_DAY ───────────────────────────────────────────────────────────

    @Test
    @DisplayName("loadOpeningToday — service_open_start_dt가 오늘인 서비스만, status 무관하게 반환")
    void openingToday_matchesByOpenDate_ignoresStatus() {
        insert("OPEN-TODAY", "예약마감", "강남구", "문화행사",
                noon(TODAY), null, null, null);          // 개시 오늘, status는 무시되어야 함
        insert("OPEN-TMRW", "접수중", "강남구", "문화행사",
                noon(TODAY.plusDays(1)), null, null, null); // 개시 내일 → 제외

        List<ScheduledServiceMatch> result =
                adapter.loadOpeningToday(SubscriptionFilter.empty(), TODAY);

        assertThat(result).extracting(ScheduledServiceMatch::serviceId).containsExactly("OPEN-TODAY");
    }

    @Test
    @DisplayName("loadOpeningToday — soft-delete된 서비스는 제외")
    void openingToday_excludesSoftDeleted() {
        insert("OPEN-DEL", "접수중", "강남구", "문화행사",
                noon(TODAY), null, null, noon(TODAY));

        List<ScheduledServiceMatch> result =
                adapter.loadOpeningToday(SubscriptionFilter.empty(), TODAY);

        assertThat(result).isEmpty();
    }

    // ── BEFORE_RECEIPT_D1 ────────────────────────────────────────────────────

    @Test
    @DisplayName("loadReceiptStartTomorrow — receipt_start가 내일 + status='접수전'만 반환")
    void receiptStartTomorrow_matchesD1AndStatusBeforeReceipt() {
        insert("D1-OK", "접수전", "강남구", "문화행사",
                null, noon(TODAY.plusDays(1)), null, null);     // D-1 + 접수전 → 포함
        insert("D1-WRONGSTATUS", "접수중", "강남구", "문화행사",
                null, noon(TODAY.plusDays(1)), null, null);     // D-1 이나 status≠접수전 → 제외
        insert("D1-TODAY", "접수전", "강남구", "문화행사",
                null, noon(TODAY), null, null);                 // 접수 시작 오늘 → 제외

        List<ScheduledServiceMatch> result =
                adapter.loadReceiptStartTomorrow(SubscriptionFilter.empty(), TODAY);

        assertThat(result).extracting(ScheduledServiceMatch::serviceId).containsExactly("D1-OK");
    }

    // ── DEADLINE_DDAY ────────────────────────────────────────────────────────

    @Test
    @DisplayName("loadDeadlineToday — receipt_end가 오늘 + status='접수중'만 반환")
    void deadlineToday_matchesDdayAndStatusReceiving() {
        insert("DD-OK", "접수중", "강남구", "문화행사",
                null, null, noon(TODAY), null);                 // 마감 오늘 + 접수중 → 포함
        insert("DD-WRONGSTATUS", "예약마감", "강남구", "문화행사",
                null, null, noon(TODAY), null);                 // 마감 오늘이나 status≠접수중 → 제외
        insert("DD-YESTERDAY", "접수중", "강남구", "문화행사",
                null, null, noon(TODAY.minusDays(1)), null);    // 어제 마감 → 제외

        List<ScheduledServiceMatch> result =
                adapter.loadDeadlineToday(SubscriptionFilter.empty(), TODAY);

        assertThat(result).extracting(ScheduledServiceMatch::serviceId).containsExactly("DD-OK");
    }

    // ── status 필터 무시 + 지역/카테고리/키워드 필터 적용 ──────────────────────

    @Test
    @DisplayName("status 필터는 무시된다 — filter.statuses에 매칭 안되는 status여도 반환")
    void statusFilter_isIgnored() {
        insert("DD-OK", "접수중", "강남구", "문화행사",
                null, null, noon(TODAY), null);

        // 사용자 필터의 statuses='접수전' 은 무시되어야 한다(트리거 고정 status='접수중' 적용).
        SubscriptionFilter filter = new SubscriptionFilter(
                Set.of("접수전"), Set.of(), Set.of(), Set.of());

        List<ScheduledServiceMatch> result = adapter.loadDeadlineToday(filter, TODAY);

        assertThat(result).extracting(ScheduledServiceMatch::serviceId).containsExactly("DD-OK");
    }

    @Test
    @DisplayName("areaNames 필터는 적용된다")
    void areaFilter_isApplied() {
        insert("DD-GANGNAM", "접수중", "강남구", "문화행사", null, null, noon(TODAY), null);
        insert("DD-SONGPA", "접수중", "송파구", "문화행사", null, null, noon(TODAY), null);

        List<ScheduledServiceMatch> result = adapter.loadDeadlineToday(
                new SubscriptionFilter(Set.of(), Set.of("강남구"), Set.of(), Set.of()), TODAY);

        assertThat(result).extracting(ScheduledServiceMatch::serviceId).containsExactly("DD-GANGNAM");
    }

    @Test
    @DisplayName("maxClassNames(카테고리) 필터는 적용된다")
    void categoryFilter_isApplied() {
        insert("DD-CULT", "접수중", "강남구", "문화행사", null, null, noon(TODAY), null);
        insert("DD-SPORT", "접수중", "강남구", "체육시설", null, null, noon(TODAY), null);

        List<ScheduledServiceMatch> result = adapter.loadDeadlineToday(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of("체육시설"), Set.of()), TODAY);

        assertThat(result).extracting(ScheduledServiceMatch::serviceId).containsExactly("DD-SPORT");
    }

    @Test
    @DisplayName("키워드 필터는 적용된다 (service_name ILIKE)")
    void keywordFilter_isApplied() {
        dsl.insertInto(PUBLIC_SERVICE_RESERVATIONS)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_ID, "DD-K1")
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_NAME, "여름 수영 강습")
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS, "접수중")
                .set(PUBLIC_SERVICE_RESERVATIONS.RECEIPT_END_DT, noon(TODAY))
                .execute();
        dsl.insertInto(PUBLIC_SERVICE_RESERVATIONS)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_ID, "DD-K2")
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_NAME, "요가 클래스")
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS, "접수중")
                .set(PUBLIC_SERVICE_RESERVATIONS.RECEIPT_END_DT, noon(TODAY))
                .execute();

        List<ScheduledServiceMatch> result = adapter.loadDeadlineToday(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of("수영"),
                        Set.of(KeywordTarget.SERVICE_NAME)), TODAY);

        assertThat(result).extracting(ScheduledServiceMatch::serviceId).containsExactly("DD-K1");
    }

    // ── [QA] day-range 경계 (UTC [d, d+1) 반열림 구간) — timezone 경계 검증 ──

    @Test
    @DisplayName("[QA] loadDeadlineToday — receipt_end가 정확히 오늘 00:00 UTC면 포함(하한 inclusive)")
    void deadlineToday_exactStartOfDayUtc_included() {
        OffsetDateTime startOfToday = TODAY.atStartOfDay().atOffset(ZoneOffset.UTC); // 00:00:00Z
        insert("DD-MIDNIGHT", "접수중", "강남구", "문화행사", null, null, startOfToday, null);

        List<ScheduledServiceMatch> result =
                adapter.loadDeadlineToday(SubscriptionFilter.empty(), TODAY);

        assertThat(result).extracting(ScheduledServiceMatch::serviceId).containsExactly("DD-MIDNIGHT");
    }

    @Test
    @DisplayName("[QA] loadDeadlineToday — receipt_end가 내일 00:00 UTC면 제외(상한 exclusive, 하루 차이 미스)")
    void deadlineToday_exactStartOfNextDayUtc_excluded() {
        OffsetDateTime startOfTomorrow = TODAY.plusDays(1).atStartOfDay().atOffset(ZoneOffset.UTC);
        insert("DD-NEXTMIDNIGHT", "접수중", "강남구", "문화행사", null, null, startOfTomorrow, null);

        List<ScheduledServiceMatch> result =
                adapter.loadDeadlineToday(SubscriptionFilter.empty(), TODAY);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("[QA] loadDeadlineToday — receipt_end가 오늘 23:59:59 UTC면 포함(구간 끝 직전)")
    void deadlineToday_endOfDayUtc_included() {
        OffsetDateTime endOfToday = TODAY.atTime(23, 59, 59).atOffset(ZoneOffset.UTC);
        insert("DD-ENDOFDAY", "접수중", "강남구", "문화행사", null, null, endOfToday, null);

        List<ScheduledServiceMatch> result =
                adapter.loadDeadlineToday(SubscriptionFilter.empty(), TODAY);

        assertThat(result).extracting(ScheduledServiceMatch::serviceId).containsExactly("DD-ENDOFDAY");
    }

    @Test
    @DisplayName("[QA] loadReceiptStartTomorrow — receipt_start가 정확히 내일(D+1) 00:00 UTC면 포함, 오늘 23:59 UTC면 제외")
    void receiptStartTomorrow_dPlusOneBoundary() {
        OffsetDateTime tomorrowMidnight = TODAY.plusDays(1).atStartOfDay().atOffset(ZoneOffset.UTC);
        OffsetDateTime todayLate = TODAY.atTime(23, 59, 59).atOffset(ZoneOffset.UTC);
        OffsetDateTime dayAfterMidnight = TODAY.plusDays(2).atStartOfDay().atOffset(ZoneOffset.UTC);
        insert("D1-TMRW-MIDNIGHT", "접수전", "강남구", "문화행사", null, tomorrowMidnight, null, null);
        insert("D1-TODAY-LATE", "접수전", "강남구", "문화행사", null, todayLate, null, null);
        insert("D1-DAYAFTER", "접수전", "강남구", "문화행사", null, dayAfterMidnight, null, null);

        List<ScheduledServiceMatch> result =
                adapter.loadReceiptStartTomorrow(SubscriptionFilter.empty(), TODAY);

        assertThat(result).extracting(ScheduledServiceMatch::serviceId)
                .containsExactly("D1-TMRW-MIDNIGHT");
    }

    @Test
    @DisplayName("[QA] loadOpeningToday — open_start가 오늘 00:00 UTC 포함, 어제 23:59 UTC 제외(하루 차이 미스)")
    void openingToday_startOfDayBoundary() {
        OffsetDateTime todayMidnight = TODAY.atStartOfDay().atOffset(ZoneOffset.UTC);
        OffsetDateTime yesterdayLate = TODAY.minusDays(1).atTime(23, 59, 59).atOffset(ZoneOffset.UTC);
        insert("OPEN-MIDNIGHT", "접수중", "강남구", "문화행사", todayMidnight, null, null, null);
        insert("OPEN-YDAYLATE", "접수중", "강남구", "문화행사", yesterdayLate, null, null, null);

        List<ScheduledServiceMatch> result =
                adapter.loadOpeningToday(SubscriptionFilter.empty(), TODAY);

        assertThat(result).extracting(ScheduledServiceMatch::serviceId).containsExactly("OPEN-MIDNIGHT");
    }

    @Test
    @DisplayName("메타(serviceName/area/status) + receipt 날짜가 ScheduledServiceMatch에 매핑된다")
    void mapsMetaAndDates() {
        OffsetDateTime end = noon(TODAY);
        insert("DD-META", "접수중", "강남구", "문화행사", null, noon(TODAY.minusDays(5)), end, null);

        List<ScheduledServiceMatch> result =
                adapter.loadDeadlineToday(SubscriptionFilter.empty(), TODAY);

        assertThat(result).hasSize(1);
        ScheduledServiceMatch m = result.get(0);
        assertThat(m.serviceName()).isEqualTo("DD-META-name");
        assertThat(m.areaName()).isEqualTo("강남구");
        assertThat(m.serviceStatus()).isEqualTo("접수중");
        assertThat(m.receiptEndDt()).isEqualTo(end.toString());
    }
}
