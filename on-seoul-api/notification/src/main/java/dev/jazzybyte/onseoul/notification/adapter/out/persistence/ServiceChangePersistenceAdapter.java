package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.KeywordTarget;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import org.jooq.Condition;
import org.jooq.DSLContext;
import org.jooq.Field;
import org.jooq.impl.DSL;
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
 * 도메인은 상태/지역/카테고리/키워드 같은 구조화된 필드만 노출.
 *
 * <p>changed_at 은 TIMESTAMPTZ 이므로 jOOQ 가 {@link OffsetDateTime} 으로 반환한다.
 * {@link Instant} 변환은 {@code .toInstant()} 한 번으로 끝난다 — 별도 ZoneId 변환 불필요.
 */
@Component
class ServiceChangePersistenceAdapter implements LoadServiceChangePort {

    /** likeIgnoreCase 의 LIKE 와일드카드 이스케이프 문자. */
    private static final char LIKE_ESCAPE = '\\';

    private final DSLContext dsl;

    ServiceChangePersistenceAdapter(DSLContext dsl) {
        this.dsl = dsl;
    }

    /**
     * service_change_log JOIN(INNER) public_service_reservations P ON L.service_id = P.service_id.
     * public_service_reservations 에 매칭 row 가 없으면 결과에서 제외된다 (JOIN 실패).
     *
     * 동적 WHERE:
     *   - lastNotifiedAt != null → L.changed_at > lastNotifiedAt   (하한, exclusive)
     *   - changedAtBefore != null → L.changed_at <= changedAtBefore (상한, inclusive)
     *   - filter.statuses      비었으면 무시, 아니면 P.service_status  IN (statuses)
     *   - filter.areaNames     비었으면 무시, 아니면 P.area_name       IN (areaNames)
     *   - filter.maxClassNames 비었으면 무시, 아니면 P.max_class_name IN (maxClassNames)
     *   - filter.keywords      비었으면 무시, 아니면 (각 키워드 × 각 KeywordTarget 컬럼)을 모두 OR
     *   - P.deleted_at IS NULL  (소프트 삭제 제외)
     *
     * <p>특정 serviceId 등식은 더 이상 없다 — 구독은 순수 조건 기반이다.
     */
    @Override
    public List<ServiceChange> loadFiltered(SubscriptionFilter filter,
                                            Instant lastNotifiedAt,
                                            Instant changedAtBefore) {
        SubscriptionFilter f = filter == null ? SubscriptionFilter.empty() : filter;

        Condition condition = PUBLIC_SERVICE_RESERVATIONS.DELETED_AT.isNull();

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
        if (!f.keywords().isEmpty()) {
            condition = condition.and(keywordCondition(f.keywords()));
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

    /**
     * 키워드 OR 절을 구성한다: (각 키워드 × 각 {@link KeywordTarget} 대상 컬럼) 모두를 OR 로 결합.
     * 하나라도 부분일치(ILIKE)하면 매칭된다.
     *
     * <p>대상 컬럼은 {@link KeywordTarget#serverDefaults()} 를 순회해 결정한다 —
     * 새 대상 추가 시 enum 값과 {@link #columnFor(KeywordTarget)} 매핑만 추가하면 된다.
     */
    private Condition keywordCondition(java.util.Set<String> keywords) {
        Condition or = DSL.noCondition();
        for (String raw : keywords) {
            String pattern = "%" + escapeLike(raw) + "%";
            for (KeywordTarget target : KeywordTarget.serverDefaults()) {
                or = or.or(columnFor(target).likeIgnoreCase(pattern, LIKE_ESCAPE));
            }
        }
        return or;
    }

    /**
     * {@link KeywordTarget} → 실제 jOOQ 컬럼 매핑.
     * 헥사고날 경계: 도메인은 enum 만 알고, 컬럼은 이 어댑터에서만 안다.
     * 새 대상 추가 시 여기에 분기 1개만 추가한다.
     */
    private Field<String> columnFor(KeywordTarget target) {
        return switch (target) {
            case SERVICE_NAME -> PUBLIC_SERVICE_RESERVATIONS.SERVICE_NAME;
            case PLACE_NAME -> PUBLIC_SERVICE_RESERVATIONS.PLACE_NAME;
        };
    }

    /**
     * LIKE 와일드카드(%, _) 와 이스케이프 문자(\) 를 이스케이프하여
     * 사용자 키워드가 와일드카드로 해석되거나 LIKE 인젝션이 발생하지 않도록 한다.
     * {@code likeIgnoreCase(pattern, '\\')} 와 함께 사용한다.
     */
    private static String escapeLike(String kw) {
        return kw.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_");
    }
}
