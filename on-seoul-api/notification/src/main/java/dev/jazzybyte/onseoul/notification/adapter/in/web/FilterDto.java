package dev.jazzybyte.onseoul.notification.adapter.in.web;

import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;

import java.util.Set;

/**
 * 구독 필터의 입출력 공통 DTO. 모든 필드는 nullable — null/empty 는 "조건 미적용" 을 의미한다.
 */
public record FilterDto(
        Set<String> statuses,
        Set<String> areaNames,
        Set<String> maxClassNames
) {
    public SubscriptionFilter toDomain() {
        return new SubscriptionFilter(statuses, areaNames, maxClassNames);
    }

    public static FilterDto fromDomain(SubscriptionFilter filter) {
        if (filter == null) {
            return new FilterDto(Set.of(), Set.of(), Set.of());
        }
        return new FilterDto(filter.statuses(), filter.areaNames(), filter.maxClassNames());
    }
}
