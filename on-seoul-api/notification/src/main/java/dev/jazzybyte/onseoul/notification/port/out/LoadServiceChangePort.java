package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;

import java.time.Instant;
import java.util.List;

public interface LoadServiceChangePort {

    /**
     * service_change_log JOIN public_service_reservations 결과를 SubscriptionFilter 조건으로 필터링하여
     * lastNotifiedAt 이후(exclusive)의 변경 이력을 조회한다.
     *
     * @param serviceId         구독 대상 서비스 ID (필수)
     * @param filter            SubscriptionFilter (null이면 empty 필터로 간주)
     * @param lastNotifiedAt    이 시각 이후 변경만 반환. null이면 전체 이력.
     */
    List<ServiceChange> loadFiltered(String serviceId, SubscriptionFilter filter, Instant lastNotifiedAt);
}
