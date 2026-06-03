package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.ScheduledServiceMatch;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.out.LoadScheduledTriggerPort;
import org.jooq.Condition;
import org.jooq.DSLContext;
import org.jooq.Field;
import org.springframework.stereotype.Component;

import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;

import static dev.jazzybyte.onseoul.jooq.Tables.PUBLIC_SERVICE_RESERVATIONS;

/**
 * 시점 트리거 조회 어댑터 — {@code public_service_reservations} 직접 조회 (모델 B).
 *
 * <p>OPEN_DAY 소스 선택: <b>reservations 직접</b>(change_log 아님).
 * 사유: 시점 잡은 status 필터를 무시하고 지역/카테고리/키워드만 적용하므로,
 * 세 트리거 모두 같은 reservations 조회 경로 + 같은 dedup(uq_nd_scheduled_dedup)로 단순화된다.
 * change_log JOIN 을 두면 "오늘 새로 등록된 개시"는 더 정밀하지만, 부분 unique 인덱스가
 * 같은 (subscription_id, service_id, dispatch_date) 재발송을 막으므로 reservations 직접 조회로 충분하다.
 *
 * <p>날짜 매칭은 인덱스 친화적 반열림 구간 {@code [d, d+1)} 로 작성한다(::date 캐스팅 회피).
 * TIMESTAMPTZ 비교 기준은 UTC 달력 날짜다(발송 잡이 UTC 기준 today 를 전달).
 *
 * <p>status 필터 무시 — {@code filter.statuses()} 를 읽지 않는다. 트리거 고유 status 만 고정 적용한다.
 */
@Component
class ScheduledTriggerPersistenceAdapter implements LoadScheduledTriggerPort {

    /** SVCSTATNM 한글 표시명 — 접수 시작 전. BEFORE_RECEIPT_D1 고정 status. */
    private static final String STATUS_BEFORE_RECEIPT = "접수전";
    /** SVCSTATNM 한글 표시명 — 접수 중. DEADLINE_DDAY 고정 status. */
    private static final String STATUS_RECEIVING = "접수중";

    private final DSLContext dsl;

    ScheduledTriggerPersistenceAdapter(DSLContext dsl) {
        this.dsl = dsl;
    }

    @Override
    public List<ScheduledServiceMatch> loadOpeningToday(SubscriptionFilter filter, LocalDate today) {
        // status 조건 없음 — 개시일 도래만.
        Condition condition = dayRange(PUBLIC_SERVICE_RESERVATIONS.SERVICE_OPEN_START_DT, today)
                .and(notDeletedAndFilter(filter));
        return fetch(condition);
    }

    @Override
    public List<ScheduledServiceMatch> loadReceiptStartTomorrow(SubscriptionFilter filter, LocalDate today) {
        Condition condition = dayRange(PUBLIC_SERVICE_RESERVATIONS.RECEIPT_START_DT, today.plusDays(1))
                .and(PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS.eq(STATUS_BEFORE_RECEIPT))
                .and(notDeletedAndFilter(filter));
        return fetch(condition);
    }

    @Override
    public List<ScheduledServiceMatch> loadDeadlineToday(SubscriptionFilter filter, LocalDate today) {
        Condition condition = dayRange(PUBLIC_SERVICE_RESERVATIONS.RECEIPT_END_DT, today)
                .and(PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS.eq(STATUS_RECEIVING))
                .and(notDeletedAndFilter(filter));
        return fetch(condition);
    }

    /** 반열림 구간 [d 00:00 UTC, d+1 00:00 UTC) — 인덱스 친화적. null 컬럼은 자동 제외된다. */
    private Condition dayRange(Field<OffsetDateTime> col, LocalDate day) {
        OffsetDateTime start = day.atStartOfDay().atOffset(ZoneOffset.UTC);
        OffsetDateTime end = day.plusDays(1).atStartOfDay().atOffset(ZoneOffset.UTC);
        return col.ge(start).and(col.lt(end));
    }

    /** deleted_at IS NULL + 지역/카테고리/키워드 필터(status 제외). */
    private Condition notDeletedAndFilter(SubscriptionFilter filter) {
        SubscriptionFilter f = filter == null ? SubscriptionFilter.empty() : filter;
        Condition condition = PUBLIC_SERVICE_RESERVATIONS.DELETED_AT.isNull();
        // status 의도적 무시.
        if (!f.areaNames().isEmpty()) {
            condition = condition.and(PUBLIC_SERVICE_RESERVATIONS.AREA_NAME.in(f.areaNames()));
        }
        if (!f.maxClassNames().isEmpty()) {
            condition = condition.and(PUBLIC_SERVICE_RESERVATIONS.MAX_CLASS_NAME.in(f.maxClassNames()));
        }
        if (!f.keywords().isEmpty()) {
            condition = condition.and(KeywordConditionBuilder.keywordCondition(f));
        }
        return condition;
    }

    private List<ScheduledServiceMatch> fetch(Condition condition) {
        return dsl.select(
                        PUBLIC_SERVICE_RESERVATIONS.SERVICE_ID,
                        PUBLIC_SERVICE_RESERVATIONS.SERVICE_NAME,
                        PUBLIC_SERVICE_RESERVATIONS.SERVICE_URL,
                        PUBLIC_SERVICE_RESERVATIONS.IMAGE_URL,
                        PUBLIC_SERVICE_RESERVATIONS.PLACE_NAME,
                        PUBLIC_SERVICE_RESERVATIONS.AREA_NAME,
                        PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS,
                        PUBLIC_SERVICE_RESERVATIONS.TARGET_INFO,
                        PUBLIC_SERVICE_RESERVATIONS.RECEIPT_START_DT,
                        PUBLIC_SERVICE_RESERVATIONS.RECEIPT_END_DT)
                .from(PUBLIC_SERVICE_RESERVATIONS)
                .where(condition)
                .orderBy(PUBLIC_SERVICE_RESERVATIONS.SERVICE_ID.asc())
                .fetch(r -> new ScheduledServiceMatch(
                        r.get(PUBLIC_SERVICE_RESERVATIONS.SERVICE_ID),
                        r.get(PUBLIC_SERVICE_RESERVATIONS.SERVICE_NAME),
                        r.get(PUBLIC_SERVICE_RESERVATIONS.SERVICE_URL),
                        r.get(PUBLIC_SERVICE_RESERVATIONS.IMAGE_URL),
                        r.get(PUBLIC_SERVICE_RESERVATIONS.PLACE_NAME),
                        r.get(PUBLIC_SERVICE_RESERVATIONS.AREA_NAME),
                        r.get(PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS),
                        r.get(PUBLIC_SERVICE_RESERVATIONS.TARGET_INFO),
                        toIsoString(r.get(PUBLIC_SERVICE_RESERVATIONS.RECEIPT_START_DT)),
                        toIsoString(r.get(PUBLIC_SERVICE_RESERVATIONS.RECEIPT_END_DT))));
    }

    private static String toIsoString(OffsetDateTime odt) {
        return odt == null ? null : odt.toString();
    }
}
