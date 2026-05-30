package dev.jazzybyte.onseoul.notification.adapter.in.web;

import com.fasterxml.jackson.annotation.JsonIgnore;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.in.CreateSubscriptionUseCase.CreateSubscriptionCommand;
import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.NotEmpty;

import java.util.Set;

/**
 * 구독 생성 요청. serviceId 고정 모델을 폐기하고 순수 조건(필터+키워드) 기반으로 전환했다.
 *
 * <p>검증:
 * <ul>
 *   <li>채널은 최소 1개 ({@link NotEmpty}).</li>
 *   <li>빈 구독 가드 — statuses/areaNames/maxClassNames/keywords 가 모두 비면 거부 (최소 1개 조건 강제).</li>
 *   <li>키워드 최대 {@link SubscriptionFilter#MAX_KEYWORDS}개, 각 키워드는 공백 불가.</li>
 * </ul>
 */
public record CreateSubscriptionRequest(
        FilterDto filter,
        @NotEmpty Set<NotificationChannel> channels
) {
    /** 빈 구독 가드: 필터 4종이 모두 비면 거부 (전체 변경 구독 방지). */
    @JsonIgnore
    @AssertTrue(message = "필터 조건(상태/지역/카테고리/키워드) 중 최소 1개는 지정해야 합니다.")
    public boolean isAtLeastOneConditionPresent() {
        return filter != null && !filter.toDomain().isEmpty();
    }

    /** 키워드 개수 제한. */
    @JsonIgnore
    @AssertTrue(message = "키워드는 최대 " + SubscriptionFilter.MAX_KEYWORDS + "개까지 지정할 수 있습니다.")
    public boolean isKeywordCountWithinLimit() {
        if (filter == null || filter.keywords() == null) {
            return true;
        }
        return filter.keywords().size() <= SubscriptionFilter.MAX_KEYWORDS;
    }

    /** 각 키워드는 null/공백일 수 없다. */
    @JsonIgnore
    @AssertTrue(message = "키워드는 공백일 수 없습니다.")
    public boolean isEachKeywordNonBlank() {
        if (filter == null || filter.keywords() == null) {
            return true;
        }
        return filter.keywords().stream().allMatch(k -> k != null && !k.isBlank());
    }

    public CreateSubscriptionCommand toCommand() {
        SubscriptionFilter f = filter != null ? filter.toDomain() : SubscriptionFilter.empty();
        return new CreateSubscriptionCommand(f, channels);
    }
}
