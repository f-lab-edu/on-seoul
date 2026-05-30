package dev.jazzybyte.onseoul.notification.domain;

/**
 * 알림 배치 실행 상태.
 * RUNNING: 실행 중. SUCCESS: 정상 종료. FAILED: 비정상 종료(배치 orchestration 자체 실패).
 */
public enum BatchStatus {
    RUNNING, SUCCESS, FAILED
}
