package dev.jazzybyte.onseoul.notification.domain;

import java.util.Collections;
import java.util.LinkedHashSet;
import java.util.Set;

/**
 * 구독 필터의 구조화된 값 객체 (ADR-0004 §SubscriptionFilter 도입).
 *
 * <p>도메인은 카테고리/지역/상태/키워드 같은 구조화된 필드만 노출하고,
 * 실제 SQL WHERE 절 생성은 어댑터(`ServiceChangePersistenceAdapter`)가 담당한다.
 * 도메인이 SQL을 알면 안 된다 — JSONB 역직렬화 결과를 그대로 들고 다닌다.
 *
 * <p>필드 의미 (모두 nullable, null/empty == 해당 조건 미적용):
 * <ul>
 *   <li>{@code statuses}      — public_service_reservations.service_status 화이트리스트</li>
 *   <li>{@code areaNames}     — public_service_reservations.area_name 화이트리스트</li>
 *   <li>{@code maxClassNames} — public_service_reservations.max_class_name 화이트리스트 (카테고리)</li>
 *   <li>{@code keywords}      — {@link KeywordTarget} 대상 컬럼들에 대한 부분일치(ILIKE) 키워드.
 *       여러 키워드 / 여러 대상 컬럼은 모두 OR 결합된다.</li>
 * </ul>
 */
public record SubscriptionFilter(
        Set<String> statuses,
        Set<String> areaNames,
        Set<String> maxClassNames,
        Set<String> keywords
) {
    /**
     * 한 구독이 가질 수 있는 키워드 최대 개수.
     * 정책 변경 시 이 상수만 조정하면 검증 로직 전체에 반영된다.
     */
    public static final int MAX_KEYWORDS = 3;

    public SubscriptionFilter {
        statuses      = nullSafe(statuses);
        areaNames     = nullSafe(areaNames);
        maxClassNames = nullSafe(maxClassNames);
        keywords      = nullSafe(keywords);
    }

    /** 모든 조건이 비어 있는 "passthrough" 필터. */
    public static SubscriptionFilter empty() {
        return new SubscriptionFilter(Set.of(), Set.of(), Set.of(), Set.of());
    }

    public boolean isEmpty() {
        return statuses.isEmpty() && areaNames.isEmpty()
                && maxClassNames.isEmpty() && keywords.isEmpty();
    }

    private static Set<String> nullSafe(Set<String> in) {
        if (in == null || in.isEmpty()) return Collections.emptySet();
        return Collections.unmodifiableSet(new LinkedHashSet<>(in));
    }
}
