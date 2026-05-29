package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import org.jooq.Condition;
import org.jooq.DSLContext;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;

import static dev.jazzybyte.onseoul.jooq.Tables.PUBLIC_SERVICE_RESERVATIONS;
import static dev.jazzybyte.onseoul.jooq.Tables.SERVICE_CHANGE_LOG;

/**
 * service_change_log JOIN public_service_reservations 결과를 SubscriptionFilter 조건으로 필터링한다.
 *
 * <p>도메인은 SQL을 알지 못한다 — 이 어댑터가 SubscriptionFilter의 필드를 읽어 WHERE 절을 동적 구성한다.
 * 도메인은 카테고리/지역/상태 같은 구조화된 필드만 노출.
 *
 * <p>changed_at 은 TIMESTAMPTZ 이므로 jOOQ 가 {@link OffsetDateTime} 으로 반환한다.
 * {@link Instant} 변환은 {@code .toInstant()} 한 번으로 끝난다 — 별도 ZoneId 변환 불필요.
 */
@Component
class ServiceChangePersistenceAdapter implements LoadServiceChangePort {

    private final DSLContext dsl;

    ServiceChangePersistenceAdapter(DSLContext dsl) {
        this.dsl = dsl;
    }

    /**
     * service_change_log JOIN(INNER) public_service_reservations P ON L.service_id = P.service_id.
     * public_service_reservations 에 매칭 row 가 없으면 결과에서 제외된다 (JOIN 실패).
     *
     * 동적 WHERE:
     *   - L.service_id = serviceId
     *   - lastNotifiedAt != null → L.changed_at > lastNotifiedAt   (하한, exclusive)
     *   - changedAtBefore != null → L.changed_at <= changedAtBefore (상한, inclusive)
     *   - filter.statuses     비었으면 무시, 아니면 P.service_status   IN (statuses)
     *   - filter.areaNames    비었으면 무시, 아니면 P.area_name        IN (areaNames)
     *   - filter.maxClassNames 비었으면 무시, 아니면 P.max_class_name IN (maxClassNames)
     *   - P.deleted_at IS NULL  (소프트 삭제 제외)
     */
    @Override
    public List<ServiceChange> loadFiltered(String serviceId,
                                            SubscriptionFilter filter,
                                            Instant lastNotifiedAt,
                                            Instant changedAtBefore) {
        SubscriptionFilter f = filter == null ? SubscriptionFilter.empty() : filter;

        Condition condition = SERVICE_CHANGE_LOG.SERVICE_ID.eq(serviceId)
                .and(PUBLIC_SERVICE_RESERVATIONS.DELETED_AT.isNull());

        if (lastNotifiedAt != null) {
            OffsetDateTime since = lastNotifiedAt.atOffset(ZoneOffset.UTC);
            condition = condition.and(SERVICE_CHANGE_LOG.CHANGED_AT.gt(since));
        }
        if (changedAtBefore != null) {
            OffsetDateTime before = changedAtBefore.atOffset(ZoneOffset.UTC);
            condition = condition.and(SERVICE_CHANGE_LOG.CHANGED_AT.le(before));
        }
        if (!f.statuses().isEmpty()) {
            condition = condition.and(PUBLIC_SERVICE_RESERVATIONS.SERVICE_STATUS.in(f.statuses()));
        }
        if (!f.areaNames().isEmpty()) {
            condition = condition.and(PUBLIC_SERVICE_RESERVATIONS.AREA_NAME.in(f.areaNames()));
        }
        if (!f.maxClassNames().isEmpty()) {
            condition = condition.and(PUBLIC_SERVICE_RESERVATIONS.MAX_CLASS_NAME.in(f.maxClassNames()));
        }

        return dsl.select(
                        SERVICE_CHANGE_LOG.ID,
                        SERVICE_CHANGE_LOG.SERVICE_ID,
                        SERVICE_CHANGE_LOG.CHANGE_TYPE,
                        SERVICE_CHANGE_LOG.FIELD_NAME,
                        SERVICE_CHANGE_LOG.OLD_VALUE,
                        SERVICE_CHANGE_LOG.NEW_VALUE,
                        SERVICE_CHANGE_LOG.CHANGED_AT)
                .from(SERVICE_CHANGE_LOG)
                .join(PUBLIC_SERVICE_RESERVATIONS)
                    .on(PUBLIC_SERVICE_RESERVATIONS.SERVICE_ID.eq(SERVICE_CHANGE_LOG.SERVICE_ID))
                .where(condition)
                .orderBy(SERVICE_CHANGE_LOG.CHANGED_AT.asc())
                .fetch(r -> {
                    OffsetDateTime odt = r.get(SERVICE_CHANGE_LOG.CHANGED_AT);
                    if (odt == null) {
                        // changed_at NOT NULL 제약이 있으므로 null은 DDL-DB 불일치를 의미한다.
                        throw new IllegalStateException(
                                "service_change_log.changed_at is null — schema mismatch?");
                    }
                    Instant changedAt = odt.toInstant();
                    return new ServiceChange(
                            r.get(SERVICE_CHANGE_LOG.ID),
                            r.get(SERVICE_CHANGE_LOG.SERVICE_ID),
                            r.get(SERVICE_CHANGE_LOG.CHANGE_TYPE),
                            r.get(SERVICE_CHANGE_LOG.FIELD_NAME),
                            r.get(SERVICE_CHANGE_LOG.OLD_VALUE),
                            r.get(SERVICE_CHANGE_LOG.NEW_VALUE),
                            changedAt);
                });
    }
}
