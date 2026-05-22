package dev.jazzybyte.onseoul.notification.domain;

/**
 * ADR-0004 per-batch 모델: PENDING/SUCCESS/FAILED 만 사용한다.
 * DEAD 상태는 last_notified_at 미갱신 정책으로 대체되어 제거되었다.
 */
public enum DispatchStatus {
    PENDING, SUCCESS, FAILED
}
