package dev.jazzybyte.onseoul.notification.domain;

/**
 * 알림 발송 트리거 종류 (모델 B — change_log + 시점 공존).
 *
 * <p>enum 이름은 {@code notification_dispatches.trigger_type} CHECK 제약
 * ({@code chk_nd_trigger_type})의 화이트리스트 문자열과 정확히 일치해야 한다.
 * JPA 매핑은 {@code @Enumerated(EnumType.STRING)} 이며, 이름이 어긋나면 INSERT 가 CHECK 에서 거부된다.
 *
 * <p>우선순위(priority): 같은 {@code (subscription_id, service_id, dispatch_date)} 에 대해
 * CHANGE(1순위)가 시점 트리거(2순위)보다 우선한다. 우선순위는 DB 제약이 아니라
 * 스케줄러 실행 순서 + 존재 확인으로 보장하는 cross-trigger dedup 의 비교 기준이다.
 *
 * <table>
 *   <tr><th>type</th><th>priority</th><th>소스</th><th>조건</th><th>status 필터</th></tr>
 *   <tr><td>CHANGE</td><td>1</td><td>change_log JOIN reservations</td><td>변경 감지 + 종료 제외</td><td>사용자 필터 적용</td></tr>
 *   <tr><td>OPEN_DAY</td><td>2</td><td>reservations 직접</td><td>service_open_start_dt = TODAY</td><td>무시</td></tr>
 *   <tr><td>BEFORE_RECEIPT_D1</td><td>2</td><td>reservations 직접</td><td>receipt_start_dt = TODAY+1, status='접수전'</td><td>무시</td></tr>
 *   <tr><td>DEADLINE_DDAY</td><td>2</td><td>reservations 직접</td><td>receipt_end_dt = TODAY, status='접수중'</td><td>무시</td></tr>
 * </table>
 */
public enum TriggerType {
    /** 변경 이벤트 기반(기존 동작). 1순위. */
    CHANGE(1),
    /** 서비스 개시일 도래. 2순위. */
    OPEN_DAY(2),
    /** 접수 시작 D-1 사전 안내. 2순위. */
    BEFORE_RECEIPT_D1(2),
    /** 접수 마감 당일. 2순위. */
    DEADLINE_DDAY(2);

    private final int priority;

    TriggerType(int priority) {
        this.priority = priority;
    }

    /** 낮을수록 우선. CHANGE=1, 시점 3종=2. */
    public int priority() {
        return priority;
    }

    /** 변경 이벤트 기반(기존 CHANGE) 트리거 여부. */
    public boolean isChange() {
        return this == CHANGE;
    }

    /** 시점 기반 트리거(OPEN_DAY/BEFORE_RECEIPT_D1/DEADLINE_DDAY) 여부 — service_id 단위로 발행된다. */
    public boolean isScheduled() {
        return this != CHANGE;
    }
}
