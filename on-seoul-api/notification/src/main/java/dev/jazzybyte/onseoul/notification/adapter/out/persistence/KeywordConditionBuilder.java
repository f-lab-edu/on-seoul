package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.KeywordTarget;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import org.jooq.Condition;
import org.jooq.Field;
import org.jooq.impl.DSL;

import java.util.Set;

import static dev.jazzybyte.onseoul.jooq.Tables.PUBLIC_SERVICE_RESERVATIONS;

/**
 * SubscriptionFilter 의 키워드 OR 절 구성 + LIKE 이스케이프 공용 헬퍼.
 *
 * <p>{@code ServiceChangePersistenceAdapter}/{@code ScheduledTriggerPersistenceAdapter} 가
 * 동일한 키워드 매칭 로직(각 키워드 × 각 KeywordTarget 컬럼 ILIKE 를 모두 OR)을 쓰므로
 * 중복을 제거한다. 동작은 양쪽 기존 구현과 동일하다 — LIKE 와일드카드/이스케이프 처리 포함.
 */
final class KeywordConditionBuilder {

    /** likeIgnoreCase 의 LIKE 와일드카드 이스케이프 문자. */
    private static final char LIKE_ESCAPE = '\\';

    private KeywordConditionBuilder() {
    }

    /**
     * 키워드 OR 절: (각 키워드 × 각 선택된 {@link KeywordTarget} 대상 컬럼) 모두를 OR 로 결합.
     * 하나라도 부분일치(ILIKE)하면 매칭된다.
     *
     * <p>대상 컬럼은 {@code filter.keywordTargets()}(사용자 선택)를 순회해 결정한다.
     * 비어 있으면 {@link KeywordTarget#serverDefaults()}(둘 다)로 fallback — 구버전 구독 보존.
     */
    static Condition keywordCondition(SubscriptionFilter filter) {
        Set<KeywordTarget> targets = filter.keywordTargets().isEmpty()
                ? KeywordTarget.serverDefaults()
                : filter.keywordTargets();
        Condition or = DSL.noCondition();
        for (String raw : filter.keywords()) {
            String pattern = "%" + escapeLike(raw) + "%";
            for (KeywordTarget target : targets) {
                or = or.or(columnFor(target).likeIgnoreCase(pattern, LIKE_ESCAPE));
            }
        }
        return or;
    }

    /**
     * {@link KeywordTarget} → 실제 jOOQ 컬럼 매핑.
     * 헥사고날 경계: 도메인은 enum 만 알고, 컬럼은 어댑터 계층에서만 안다.
     * 새 대상 추가 시 여기에 분기 1개만 추가한다.
     */
    private static Field<String> columnFor(KeywordTarget target) {
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
