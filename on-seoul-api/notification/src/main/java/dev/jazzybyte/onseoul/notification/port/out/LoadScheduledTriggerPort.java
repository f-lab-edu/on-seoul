package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.ScheduledServiceMatch;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;

import java.time.LocalDate;
import java.util.List;

/**
 * 시점 기반 트리거 조회 out 포트 (모델 B).
 *
 * <p>{@code public_service_reservations} 를 직접 조회한다. change_log 와 무관하게
 * "오늘이 그 날짜라서" 발화하는 알림을 위해 트리거별 고유 날짜/상태 조건을 적용한다.
 *
 * <p>중요 — status 필터 무시:
 * 세 메서드 모두 {@link SubscriptionFilter} 의 지역(areaNames)/카테고리(maxClassNames)/키워드(keywords)
 * 만 적용한다. {@code filter.statuses()} 는 무시한다. status 는 트리거 고유 조건으로 고정된다
 * (D-1 → '접수전', 마감 → '접수중', 개시 → status 조건 없음).
 *
 * <p>모든 조회는 {@code deleted_at IS NULL} 을 전제로 한다(소프트 삭제 제외).
 */
public interface LoadScheduledTriggerPort {

    /**
     * 서비스 개시일이 오늘인 서비스. status 조건 없음.
     * 조건: {@code service_open_start_dt::date = today AND deleted_at IS NULL} + 필터(지역/카테고리/키워드).
     */
    List<ScheduledServiceMatch> loadOpeningToday(SubscriptionFilter filter, LocalDate today);

    /**
     * 접수 시작이 내일(D-1)인 서비스. status='접수전' 고정.
     * 조건: {@code receipt_start_dt::date = today+1 AND service_status='접수전' AND deleted_at IS NULL}.
     */
    List<ScheduledServiceMatch> loadReceiptStartTomorrow(SubscriptionFilter filter, LocalDate today);

    /**
     * 접수 마감이 오늘(D-day)인 서비스. status='접수중' 고정.
     * 조건: {@code receipt_end_dt::date = today AND service_status='접수중' AND deleted_at IS NULL}.
     */
    List<ScheduledServiceMatch> loadDeadlineToday(SubscriptionFilter filter, LocalDate today);
}
