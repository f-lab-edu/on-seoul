package dev.jazzybyte.onseoul.notification.adapter.in.web;

import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;

import java.util.Set;

/**
 * 구독 필터의 입출력 공통 DTO. 모든 필드는 nullable — null/empty 는 "조건 미적용" 을 의미한다.
 *
 * <p>{@code keywords} 는 service_name/place_name 부분일치(ILIKE) 대상이다.
 * 사용자는 매칭 대상 컬럼을 직접 선택하지 않는다 — 서버가 설정한 전체 대상에 매칭한다.
 */
public record FilterDto(
        Set<String> statuses,
        Set<String> areaNames,
        Set<String> maxClassNames,
        Set<String> keywords
) {
    public SubscriptionFilter toDomain() {
        return new SubscriptionFilter(statuses, areaNames, maxClassNames, keywords);
    }

    public static FilterDto fromDomain(SubscriptionFilter filter) {
        if (filter == null) {
            return new FilterDto(Set.of(), Set.of(), Set.of(), Set.of());
        }
        return new FilterDto(filter.statuses(), filter.areaNames(),
                filter.maxClassNames(), filter.keywords());
    }
}
