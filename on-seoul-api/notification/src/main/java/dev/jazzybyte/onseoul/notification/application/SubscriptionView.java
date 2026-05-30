package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;

import java.time.Instant;
import java.util.Set;

/**
 * 알림 구독 조회용 read model. 도메인 {@link dev.jazzybyte.onseoul.notification.domain.NotificationSubscription}
 * 에서 인바운드 어댑터(컨트롤러)로 노출되는 표현이다.
 *
 * <p>도메인의 {@code filter} 는 JSONB 문자열이지만 view 는 이미 파싱된
 * {@link SubscriptionFilter} 를 들고 있어 인바운드 어댑터가 outbound port 에 의존하지 않아도 된다.
 */
public record SubscriptionView(
        Long id,
        Long userId,
        String serviceId,
        SubscriptionFilter filter,
        Set<NotificationChannel> channels,
        Instant lastNotifiedAt,
        Instant createdAt
) {}
