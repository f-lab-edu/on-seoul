package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import org.jooq.Condition;
import org.jooq.DSLContext;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;

import static dev.jazzybyte.onseoul.jooq.Tables.PUBLIC_SERVICE_RESERVATIONS;
import static dev.jazzybyte.onseoul.jooq.Tables.SERVICE_CHANGE_LOG;

/**
 * service_change_log JOIN public_service_reservations 결과를 SubscriptionFilter 조건으로 필터링한다.
 *
 * <p>도메인은 SQL을 알지 못한다 — 이 어댑터가 SubscriptionFilter의 필드를 읽어 WHERE 절을 동적 구성한다.
 * 도메인은 카테고리/지역/상태 같은 구조화된 필드만 노출.
 */
@Component
class ServiceChangePersistenceAdapter implements LoadServiceChangePort {

    private static final ZoneId ZONE_SEOUL = ZoneId.of("Asia/Seoul");

    private final DSLContext dsl;

    ServiceChangePersistenceAdapter(DSLContext dsl) {
        this.dsl = dsl;
    }

    /**
     * service_change_log L JOIN public_service_reservations P ON L.service_id = P.service_id.
     *
     * 동적 WHERE:
     *   - L.service_id = serviceId
     *   - lastNotifiedAt != null → L.changed_at > lastNotifiedAt
     *   - filter.statuses     비었으면 무시, 아니면 P.service_status   IN (statuses)
     *   - filter.areaNames    비었으면 무시, 아니면 P.area_name        IN (areaNames)
     *   - filter.maxClassNames 비었으면 무시, 아니면 P.max_class_name IN (maxClassNames)
     *   - P.deleted_at IS NULL  (소프트 삭제 제외)
     */
    @Override
    public List<ServiceChange> loadFiltered(String serviceId,
                                            SubscriptionFilter filter,
                                            Instant lastNotifiedAt) {
        SubscriptionFilter f = filter == null ? SubscriptionFilter.empty() : filter;

        Condition condition = SERVICE_CHANGE_LOG.SERVICE_ID.eq(serviceId)
                .and(PUBLIC_SERVICE_RESERVATIONS.DELETED_AT.isNull());

        if (lastNotifiedAt != null) {
            LocalDateTime since = LocalDateTime.ofInstant(lastNotifiedAt, ZONE_SEOUL);
            condition = condition.and(SERVICE_CHANGE_LOG.CHANGED_AT.gt(since));
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
                    LocalDateTime ldt = r.get(SERVICE_CHANGE_LOG.CHANGED_AT);
                    Instant changedAt = (ldt != null)
                            ? ldt.atZone(ZONE_SEOUL).toInstant()
                            : Instant.now();
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
