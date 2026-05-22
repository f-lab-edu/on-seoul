package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;

/**
 * NotificationSubscription.filter (JSONB 문자열) → 도메인 {@link SubscriptionFilter} 변환 포트.
 *
 * <p>도메인/애플리케이션 레이어가 Jackson 같은 직렬화 프레임워크에 의존하지 않도록 어댑터에 위임한다.
 * (헥사고날 — application은 adapter 패키지에 의존하지 못함)
 */
public interface SubscriptionFilterParserPort {
    SubscriptionFilter parse(String filterJson);
}
