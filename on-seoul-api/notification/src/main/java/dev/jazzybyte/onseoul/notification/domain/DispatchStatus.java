package dev.jazzybyte.onseoul.notification.domain;

/**
 * ADR-0004 per-batch 모델.
 * DEAD는 outbox retry 스케줄러가 attempt_count >= 5에서 전환하는 영구 중단 상태.
 */
public enum DispatchStatus {
    PENDING, SUCCESS, FAILED,
    /** 재시도 한도 초과로 영구 중단. */
    DEAD,
    /**
     * createdAt 기준 max-age 초과로 stale 폐기.
     * 저장된 콘텐츠가 오래되어 재발송보다 폐기가 안전한 경우.
     * 재시도 소진을 의미하는 {@link #DEAD}와 구분한다.
     */
    EXPIRED
}
