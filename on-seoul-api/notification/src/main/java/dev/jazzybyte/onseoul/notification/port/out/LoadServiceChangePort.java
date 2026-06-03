package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;

import java.time.Instant;
import java.time.LocalDate;
import java.util.List;

public interface LoadServiceChangePort {

    /**
     * service_change_log JOIN public_service_reservations 결과를 SubscriptionFilter 조건으로 필터링하여
     * [lastNotifiedAt, changedAtBefore] 범위의 변경 이력을 조회한다.
     *
     * <p>구독은 더 이상 특정 serviceId에 고정되지 않는다 — 필터(상태/지역/카테고리/키워드) 조건에
     * 매칭되는 모든 서비스의 변경 이력을 반환한다.
     *
     * <p>상한({@code changedAtBefore})과 하한({@code lastNotifiedAt})을 일치시켜
     * 배치 처리 중 발생한 변경이 다음 tick에서 중복 발송되는 것을 방지한다.
     * 메인 배치에서는 {@code batch.startedAt}을 상한으로 전달하고, TX B에서도
     * 동일한 값을 {@code last_notified_at} 커서로 전진시킨다.
     *
     * <p>종료 서비스 제외(모델 B)의 D-day 기준은 <b>UTC 달력 날짜</b>다. 호출자는 시점 트리거
     * 스케줄러와 동일한 UTC Clock 기반 {@code LocalDate.now(clock)} 을 {@code today} 로 넘겨야
     * 두 알림 경로의 D-day 경계가 일치한다(PG 세션 TimeZone GUC 비의존).
     *
     * @param filter           SubscriptionFilter (null이면 empty 필터로 간주)
     * @param lastNotifiedAt   하한(exclusive). null이면 하한 없음.
     * @param changedAtBefore  상한(inclusive). null이면 상한 없음.
     * @param today            종료 서비스 제외 기준이 되는 UTC 달력 날짜. receipt_end_dt가
     *                         {@code today} 00:00 UTC 이전이면 종료로 보고 제외한다.
     */
    List<ServiceChange> loadFiltered(SubscriptionFilter filter,
                                     Instant lastNotifiedAt, Instant changedAtBefore,
                                     LocalDate today);
}
