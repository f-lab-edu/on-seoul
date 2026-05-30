package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;

import java.util.Set;

public interface SaveSubscriptionPort {

    NotificationSubscription save(NotificationSubscription subscription);

    void saveIfAbsent(NotificationSubscription subscription);

    /**
     * 새 구독을 INSERT 한다. DB 제약 위반 시
     * {@link org.springframework.dao.DataIntegrityViolationException} 를 그대로 던진다.
     * 호출자(application service)가 이를 {@code SUBSCRIPTION_CONFLICT} 로 변환한다.
     *
     * <p>구독 중복 방지 제약(uq_ns_user_service)은 제거되었다 —
     * 한 user_id가 여러 조건 기반 구독을 가질 수 있다.
     */
    NotificationSubscription insert(NotificationSubscription subscription);

    /**
     * 구독의 filter 또는 channels 만 부분 업데이트한다.
     * lastNotifiedAt 등 발송 흐름이 갱신하는 필드는 건드리지 않는다.
     *
     * <p>application 레이어가 JSON 직렬화 책임을 갖지 않도록 도메인 타입만 받는다.
     * 직렬화는 어댑터의 persistence mapper 가 일괄 처리한다.
     *
     * @param id       대상 subscription id
     * @param filter   nullable — null 이면 filter 미변경
     * @param channels nullable — null 이면 channels 미변경
     */
    NotificationSubscription updatePartial(Long id, SubscriptionFilter filter, Set<NotificationChannel> channels);

    void deleteById(Long id);
}
