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

    private void insertReservationWithNames(String serviceId, String serviceName, String placeName) {
        dsl.insertInto(PUBLIC_SERVICE_RESERVATIONS)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_ID, serviceId)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_NAME, serviceName)
                .set(PUBLIC_SERVICE_RESERVATIONS.PLACE_NAME, placeName)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS, "RECEIVING")
                .set(PUBLIC_SERVICE_RESERVATIONS.AREA_NAME, "강남구")
                .set(PUBLIC_SERVICE_RESERVATIONS.MAX_CLASS_NAME, "문화행사")
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
    @DisplayName("필터 비어 있으면 since=null 시 (삭제되지 않은 예약의) 전체 변경 이력 반환")
    void loadFiltered_emptyFilter_returnsAll() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        insertReservation("OA-2266", "RECEIVING", "송파구", "체육시설");

        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now.minusHours(2));
        insertChange("OA-2269", "UPDATED", "service_name", "구", "신", now.minusHours(1));
        insertChange("OA-2266", "NEW", null, null, null, now);

        List<ServiceChange> result = adapter.loadFiltered(SubscriptionFilter.empty(), null, null);

        // serviceId pin이 제거되어 빈 필터는 모든 변경(전체 구독)을 매칭한다.
        assertThat(result).hasSize(3);
        assertThat(result).extracting(ServiceChange::serviceId)
                .containsExactlyInAnyOrder("OA-2269", "OA-2269", "OA-2266");
    }

    @Test
    @DisplayName("since 이후(exclusive) 데이터만 반환")
    void loadFiltered_withSince_returnsOnlyAfter() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        OffsetDateTime base = utc(2026, 5, 1, 12, 0);
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", base);
        insertChange("OA-2269", "UPDATED", "service_name", "구", "신", base.plusHours(1));

        Instant since = base.toInstant();
        List<ServiceChange> result = adapter.loadFiltered(SubscriptionFilter.empty(), since, null);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).fieldName()).isEqualTo("service_name");
    }

    @Test
    @DisplayName("changedAtBefore(상한, inclusive) — 해당 시각 이후 변경은 제외된다")
    void loadFiltered_withChanedAtBefore_excludesAfterUpperBound() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        OffsetDateTime base = utc(2026, 5, 1, 12, 0);
        insertChange("OA-2269", "UPDATED", "f1", null, null, base);
        insertChange("OA-2269", "UPDATED", "f2", null, null, base.plusHours(1));
        insertChange("OA-2269", "UPDATED", "f3", null, null, base.plusHours(2));

        // 상한: base+1h (inclusive) → f1, f2만 포함되어야 한다
        Instant before = base.plusHours(1).toInstant();
        List<ServiceChange> result = adapter.loadFiltered(SubscriptionFilter.empty(), null, before);

        assertThat(result).hasSize(2);
        assertThat(result).extracting(ServiceChange::fieldName).containsExactly("f1", "f2");
    }

    @Test
    @DisplayName("public_service_reservations에 매칭 row 없으면 빈 결과 (JOIN 실패)")
    void loadFiltered_noMatchingReservation_returnsEmpty() {
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        List<ServiceChange> result = adapter.loadFiltered(SubscriptionFilter.empty(), null, null);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("deleted_at이 NULL이 아니면 결과에서 제외된다")
    void loadFiltered_softDeletedReservation_excluded() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사", OffsetDateTime.now(ZoneOffset.UTC));
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        List<ServiceChange> result = adapter.loadFiltered(SubscriptionFilter.empty(), null, null);

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
                new SubscriptionFilter(Set.of("RECEIVING"), Set.of(), Set.of(), Set.of()),
                null, null);
        assertThat(filtered).isEmpty();

        // RECEIVING으로 변경하면 매칭
        dsl.update(PUBLIC_SERVICE_RESERVATIONS)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS, "RECEIVING")
                .where(PUBLIC_SERVICE_RESERVATIONS.SERVICE_ID.eq("OA-2269"))
                .execute();
        List<ServiceChange> matched = adapter.loadFiltered(
                new SubscriptionFilter(Set.of("RECEIVING"), Set.of(), Set.of(), Set.of()),
                null, null);
        assertThat(matched).hasSize(1);
    }

    @Test
    @DisplayName("areaNames 필터 — area_name이 매칭하지 않으면 제외")
    void loadFiltered_areaNamesFilter_filtersOut() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        List<ServiceChange> miss = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of("송파구"), Set.of(), Set.of()),
                null, null);
        assertThat(miss).isEmpty();

        List<ServiceChange> hit = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of("강남구"), Set.of(), Set.of()),
                null, null);
        assertThat(hit).hasSize(1);
    }

    @Test
    @DisplayName("maxClassNames 필터 — max_class_name이 매칭하지 않으면 제외")
    void loadFiltered_maxClassNamesFilter_filtersOut() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-2269", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        List<ServiceChange> miss = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of("체육시설"), Set.of()),
                null, null);
        assertThat(miss).isEmpty();

        List<ServiceChange> hit = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of("문화행사"), Set.of()),
                null, null);
        assertThat(hit).hasSize(1);
    }

    @Test
    @DisplayName("keywords 필터 — service_name에 키워드가 포함되면 매칭 (ILIKE)")
    void loadFiltered_keyword_matchesServiceName() {
        insertReservationWithNames("OA-1", "여름 수영 강습", "강남스포츠센터");
        insertReservationWithNames("OA-2", "요가 클래스", "송파문화회관");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-1", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);
        insertChange("OA-2", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        List<ServiceChange> result = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of("수영")),
                null, null);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).serviceId()).isEqualTo("OA-1");
    }

    @Test
    @DisplayName("keywords 필터 — place_name에만 포함돼도 매칭 (service_name/place_name OR)")
    void loadFiltered_keyword_matchesPlaceName() {
        insertReservationWithNames("OA-1", "문화 강좌", "강남수영장");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-1", "UPDATED", "service_status", "RECEIVING", "CLOSED", now);

        List<ServiceChange> result = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of("수영")),
                null, null);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).serviceId()).isEqualTo("OA-1");
    }

    @Test
    @DisplayName("keywords 필터 — 복수 키워드는 OR 결합되어 어느 하나라도 포함되면 매칭")
    void loadFiltered_multipleKeywords_orCombined() {
        insertReservationWithNames("OA-1", "여름 수영 강습", "강남센터");
        insertReservationWithNames("OA-2", "요가 클래스", "송파회관");
        insertReservationWithNames("OA-3", "독서 모임", "마포도서관");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-1", "UPDATED", "f", null, null, now);
        insertChange("OA-2", "UPDATED", "f", null, null, now);
        insertChange("OA-3", "UPDATED", "f", null, null, now);

        List<ServiceChange> result = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of("수영", "요가")),
                null, null);

        assertThat(result).extracting(ServiceChange::serviceId)
                .containsExactlyInAnyOrder("OA-1", "OA-2");
    }

    @Test
    @DisplayName("keywords 필터 — LIKE 와일드카드(%)는 리터럴로 이스케이프되어 전체 매칭하지 않는다")
    void loadFiltered_keyword_escapesWildcard() {
        insertReservationWithNames("OA-1", "여름 수영 강습", "강남센터");
        insertReservationWithNames("OA-2", "100% 환불 행사", "송파회관");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-1", "UPDATED", "f", null, null, now);
        insertChange("OA-2", "UPDATED", "f", null, null, now);

        // "%" 키워드가 와일드카드로 동작하면 모든 row가 매칭되지만, 이스케이프되면
        // "%" 리터럴을 포함한 OA-2만 매칭되어야 한다.
        List<ServiceChange> result = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of("%")),
                null, null);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).serviceId()).isEqualTo("OA-2");
    }

    @Test
    @DisplayName("keywords 필터 — '_' 와일드카드는 리터럴로 이스케이프된다 (단일 문자 매칭 방지)")
    void loadFiltered_keyword_escapesUnderscore() {
        // '_' 가 LIKE 단일문자 와일드카드로 동작하면 "AxB" 같은 임의 1문자도 매칭된다.
        // 이스케이프되면 리터럴 '_' 를 포함한 OA-2만 매칭되어야 한다.
        insertReservationWithNames("OA-1", "AxB 강좌", "강남센터");
        insertReservationWithNames("OA-2", "A_B 강좌", "송파회관");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-1", "UPDATED", "f", null, null, now);
        insertChange("OA-2", "UPDATED", "f", null, null, now);

        List<ServiceChange> result = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of("A_B")),
                null, null);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).serviceId()).isEqualTo("OA-2");
    }

    @Test
    @DisplayName("keywords 필터 — 백슬래시(\\)는 리터럴로 이스케이프된다 (이스케이프 문자 오작동 방지)")
    void loadFiltered_keyword_escapesBackslash() {
        insertReservationWithNames("OA-1", "정상 강좌", "강남센터");
        insertReservationWithNames("OA-2", "a\\b 강좌", "송파회관");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-1", "UPDATED", "f", null, null, now);
        insertChange("OA-2", "UPDATED", "f", null, null, now);

        // 키워드 "a\b" — '\' 가 이스케이프되지 않으면 LIKE 가 다음 문자를 이스케이프해 매칭이 깨진다.
        List<ServiceChange> result = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of("a\\b")),
                null, null);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).serviceId()).isEqualTo("OA-2");
    }

    @Test
    @DisplayName("keywords 필터 — 한글/유니코드 부분일치 ILIKE 가 정상 매칭된다")
    void loadFiltered_keyword_koreanUnicodeIlike() {
        insertReservationWithNames("OA-1", "여름 수영 강습", "강남스포츠센터");
        insertReservationWithNames("OA-2", "겨울 스키 캠프", "송파문화회관");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-1", "UPDATED", "f", null, null, now);
        insertChange("OA-2", "UPDATED", "f", null, null, now);

        // 한글 부분 문자열 매칭 — collation 무관하게 ILIKE '%수영%' 로 OA-1만 매칭
        List<ServiceChange> result = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of("수영")),
                null, null);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).serviceId()).isEqualTo("OA-1");
    }

    @Test
    @DisplayName("keywords + statuses — 다른 필터 차원과 AND 결합되어 둘 다 만족해야 매칭")
    void loadFiltered_keywordAndStatuses_andCombined() {
        // 키워드는 만족하나 상태 불일치 → 제외
        insertReservationWithNames("OA-1", "여름 수영 강습", "강남센터");
        dsl.update(PUBLIC_SERVICE_RESERVATIONS)
                .set(PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS, "CLOSED")
                .where(PUBLIC_SERVICE_RESERVATIONS.SERVICE_ID.eq("OA-1"))
                .execute();
        // 키워드·상태 모두 만족 → 포함
        insertReservationWithNames("OA-2", "여름 수영 교실", "송파센터"); // status=RECEIVING (helper 기본)
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-1", "UPDATED", "f", null, null, now);
        insertChange("OA-2", "UPDATED", "f", null, null, now);

        List<ServiceChange> result = adapter.loadFiltered(
                new SubscriptionFilter(Set.of("RECEIVING"), Set.of(), Set.of(), Set.of("수영")),
                null, null);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).serviceId()).isEqualTo("OA-2");
    }

    @Test
    @DisplayName("keywords — 정확히 3개(MAX_KEYWORDS)도 OR 결합되어 모두 매칭 후보가 된다")
    void loadFiltered_threeKeywords_allOrCombined() {
        insertReservationWithNames("OA-1", "수영 강습", "강남센터");
        insertReservationWithNames("OA-2", "요가 클래스", "송파회관");
        insertReservationWithNames("OA-3", "독서 모임", "마포도서관");
        insertReservationWithNames("OA-4", "축구 교실", "은평구장");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-1", "UPDATED", "f", null, null, now);
        insertChange("OA-2", "UPDATED", "f", null, null, now);
        insertChange("OA-3", "UPDATED", "f", null, null, now);
        insertChange("OA-4", "UPDATED", "f", null, null, now);

        List<ServiceChange> result = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of("수영", "요가", "독서")),
                null, null);

        assertThat(result).extracting(ServiceChange::serviceId)
                .containsExactlyInAnyOrder("OA-1", "OA-2", "OA-3");
    }

    @Test
    @DisplayName("keywordTargets={PLACE_NAME} — service_name에만 키워드 있으면 매칭 안됨(대상 부분집합)")
    void loadFiltered_keywordTargetPlaceNameOnly_doesNotMatchServiceName() {
        insertReservationWithNames("OA-1", "여름 수영 강습", "강남센터");   // service_name에만 "수영"
        insertReservationWithNames("OA-2", "요가 클래스", "송파수영장");     // place_name에만 "수영"
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-1", "UPDATED", "f", null, null, now);
        insertChange("OA-2", "UPDATED", "f", null, null, now);

        List<ServiceChange> result = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of("수영"),
                        Set.of(dev.jazzybyte.onseoul.notification.domain.KeywordTarget.PLACE_NAME)),
                null, null);

        assertThat(result).extracting(ServiceChange::serviceId).containsExactly("OA-2");
    }

    @Test
    @DisplayName("keywordTargets={SERVICE_NAME} — place_name에만 키워드 있으면 매칭 안됨")
    void loadFiltered_keywordTargetServiceNameOnly_doesNotMatchPlaceName() {
        insertReservationWithNames("OA-1", "여름 수영 강습", "강남센터");
        insertReservationWithNames("OA-2", "요가 클래스", "송파수영장");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-1", "UPDATED", "f", null, null, now);
        insertChange("OA-2", "UPDATED", "f", null, null, now);

        List<ServiceChange> result = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of("수영"),
                        Set.of(dev.jazzybyte.onseoul.notification.domain.KeywordTarget.SERVICE_NAME)),
                null, null);

        assertThat(result).extracting(ServiceChange::serviceId).containsExactly("OA-1");
    }

    @Test
    @DisplayName("keywordTargets 비면 serverDefaults(둘 다)로 fallback — 어느 컬럼이든 매칭")
    void loadFiltered_emptyTargets_fallsBackToBoth() {
        insertReservationWithNames("OA-1", "여름 수영 강습", "강남센터");
        insertReservationWithNames("OA-2", "요가 클래스", "송파수영장");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        insertChange("OA-1", "UPDATED", "f", null, null, now);
        insertChange("OA-2", "UPDATED", "f", null, null, now);

        // 4-인자 생성자 → keywordTargets 빈 집합 → 어댑터가 serverDefaults로 fallback
        List<ServiceChange> result = adapter.loadFiltered(
                new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of("수영")),
                null, null);

        assertThat(result).extracting(ServiceChange::serviceId)
                .containsExactlyInAnyOrder("OA-1", "OA-2");
    }

    @Test
    @DisplayName("결과는 changed_at ASC 순으로 정렬된다")
    void loadFiltered_orderedByChangedAtAsc() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        OffsetDateTime base = utc(2026, 5, 1, 10, 0);
        insertChange("OA-2269", "UPDATED", "f1", null, null, base.plusHours(2));
        insertChange("OA-2269", "UPDATED", "f2", null, null, base.plusHours(1));
        insertChange("OA-2269", "UPDATED", "f3", null, null, base);

        List<ServiceChange> result = adapter.loadFiltered(SubscriptionFilter.empty(), null, null);

        assertThat(result).extracting(ServiceChange::fieldName)
                .containsExactly("f3", "f2", "f1");
    }

    @Test
    @DisplayName("changedAt은 TIMESTAMPTZ에서 Instant으로 정확히 변환된다")
    void loadFiltered_changedAtConvertedToInstant() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        OffsetDateTime insertedAt = utc(2026, 5, 1, 12, 0);
        insertChange("OA-2269", "NEW", "service_status", null, "RECEIVING", insertedAt);

        List<ServiceChange> result = adapter.loadFiltered(SubscriptionFilter.empty(), null, null);

        assertThat(result.get(0).changedAt()).isEqualTo(insertedAt.toInstant());
    }

    // ── changedAtBefore 상한 추가 엣지케이스 (커밋 1 보완) ─────────────────

    @Test
    @DisplayName("changedAtBefore = lastNotifiedAt — 하한(exclusive)과 상한(inclusive)이 동일하면 결과 0건")
    void loadFiltered_upperBoundEqualsLowerBound_returnsEmpty() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        OffsetDateTime base = utc(2026, 5, 1, 12, 0);
        insertChange("OA-2269", "UPDATED", "f1", null, null, base);

        // lastNotifiedAt == changedAtBefore == base.toInstant()
        // 하한은 exclusive(changed_at > since), 상한은 inclusive(changed_at <= before)
        // changed_at = base, since = base → changed_at > since 는 false → 결과 없음
        Instant pivot = base.toInstant();
        List<ServiceChange> result = adapter.loadFiltered(SubscriptionFilter.empty(), pivot, pivot);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("changedAtBefore가 lastNotifiedAt보다 이전이면 결과 0건")
    void loadFiltered_upperBoundBeforeLowerBound_returnsEmpty() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        OffsetDateTime base = utc(2026, 5, 1, 12, 0);
        insertChange("OA-2269", "UPDATED", "f1", null, null, base.plusHours(1));

        Instant lastNotified = base.plusHours(2).toInstant(); // 하한이 상한보다 나중
        Instant changedAtBefore = base.toInstant();           // 상한이 하한보다 이전

        List<ServiceChange> result = adapter.loadFiltered(SubscriptionFilter.empty(),
                lastNotified, changedAtBefore);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("lastNotifiedAt과 changedAtBefore 양쪽 모두 지정 — 범위 내 row만 반환")
    void loadFiltered_bothBoundsSpecified_returnsOnlyInRange() {
        insertReservation("OA-2269", "RECEIVING", "강남구", "문화행사");
        OffsetDateTime base = utc(2026, 5, 1, 12, 0);
        insertChange("OA-2269", "UPDATED", "before_lower", null, null, base.minusHours(1)); // 제외
        insertChange("OA-2269", "UPDATED", "at_lower",    null, null, base);               // 제외 (exclusive)
        insertChange("OA-2269", "UPDATED", "in_range",   null, null, base.plusHours(1));  // 포함
        insertChange("OA-2269", "UPDATED", "at_upper",   null, null, base.plusHours(2));  // 포함 (inclusive)
        insertChange("OA-2269", "UPDATED", "after_upper", null, null, base.plusHours(3)); // 제외

        Instant lower = base.toInstant();
        Instant upper = base.plusHours(2).toInstant();

        List<ServiceChange> result = adapter.loadFiltered(SubscriptionFilter.empty(), lower, upper);

        assertThat(result).hasSize(2);
        assertThat(result).extracting(ServiceChange::fieldName).containsExactly("in_range", "at_upper");
    }
}
