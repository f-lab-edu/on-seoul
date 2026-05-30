package dev.jazzybyte.onseoul.notification.adapter.in.web;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.notification.domain.KeywordTarget;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;

import java.util.LinkedHashSet;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * 구독 필터의 입출력 공통 DTO. 모든 필드는 nullable — null/empty 는 "조건 미적용" 을 의미한다.
 *
 * <p>{@code keywords} 는 service_name/place_name 부분일치(ILIKE) 대상이다.
 * {@code keywordTargets} 는 키워드를 어느 컬럼에 매칭할지 사용자가 화면에서 고른 대상이다
 * ({@code "SERVICE_NAME"} / {@code "PLACE_NAME"}). enum 을 와이어에 직접 노출하지 않고
 * String 으로 주고받는다(미래 rename 시 와이어 호환 보호). 미인식 값은 400(INVALID_INPUT).
 * 비어 있으면 service 계층에서 둘 다로 정규화된다.
 */
public record FilterDto(
        Set<String> statuses,
        Set<String> areaNames,
        Set<String> maxClassNames,
        Set<String> keywords,
        Set<String> keywordTargets
) {
    /** keywordTargets 미지정 4-인자 편의 생성자 (대상은 service 계층에서 정규화). */
    public FilterDto(Set<String> statuses, Set<String> areaNames,
                     Set<String> maxClassNames, Set<String> keywords) {
        this(statuses, areaNames, maxClassNames, keywords, Set.of());
    }

    public SubscriptionFilter toDomain() {
        return new SubscriptionFilter(statuses, areaNames, maxClassNames, keywords,
                toKeywordTargets(keywordTargets));
    }

    public static FilterDto fromDomain(SubscriptionFilter filter) {
        if (filter == null) {
            return new FilterDto(Set.of(), Set.of(), Set.of(), Set.of(), Set.of());
        }
        Set<String> targets = filter.keywordTargets().stream()
                .map(KeywordTarget::name)
                .collect(Collectors.toCollection(LinkedHashSet::new));
        return new FilterDto(filter.statuses(), filter.areaNames(),
                filter.maxClassNames(), filter.keywords(), targets);
    }

    private static Set<KeywordTarget> toKeywordTargets(Set<String> raw) {
        if (raw == null || raw.isEmpty()) {
            return Set.of();
        }
        Set<KeywordTarget> out = new LinkedHashSet<>();
        for (String v : raw) {
            if (v == null || v.isBlank()) {
                continue;
            }
            try {
                out.add(KeywordTarget.valueOf(v));
            } catch (IllegalArgumentException ex) {
                throw new OnSeoulApiException(ErrorCode.INVALID_INPUT,
                        "지원하지 않는 키워드 매칭 대상입니다: " + v);
            }
        }
        return out;
    }
}
