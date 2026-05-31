package dev.jazzybyte.onseoul.notification.domain;

import lombok.Getter;

import java.time.Instant;

/**
 * 배치 × 구독 단위 알림 발송 이력 (ADR-0004).
 *
 * <p>per-change 모델에서 제거된 필드: {@code changeLogId}.
 * 추가된 필드: {@code batchId}, {@code attemptCount}.
 */
@Getter
public class NotificationDispatch {

    public static final int MAX_ATTEMPTS = 5;

    private Long id;
    private Long batchId;
    private Long subscriptionId;
    private DispatchStatus status;
    private Instant sentAt;
    private String generatedTitle;
    private String generatedBody;
    private TemplateSource templateSource;
    private String lastError;
    private int attemptCount;
    private Instant createdAt;
    private Instant updatedAt;

    /** Reconstitute from persistence. */
    public NotificationDispatch(Long id, Long batchId, Long subscriptionId,
                                DispatchStatus status,
                                Instant sentAt, String generatedTitle, String generatedBody,
                                TemplateSource templateSource, String lastError,
                                int attemptCount,
                                Instant createdAt, Instant updatedAt) {
        this.id = id;
        this.batchId = batchId;
        this.subscriptionId = subscriptionId;
        this.status = status;
        this.sentAt = sentAt;
        this.generatedTitle = generatedTitle;
        this.generatedBody = generatedBody;
        this.templateSource = templateSource;
        this.lastError = lastError;
        this.attemptCount = attemptCount;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    private NotificationDispatch() {}

    /** Factory: 신규 PENDING dispatch 생성. */
    public static NotificationDispatch create(Long batchId, Long subscriptionId) {
        NotificationDispatch d = new NotificationDispatch();
        d.batchId = batchId;
        d.subscriptionId = subscriptionId;
        d.status = DispatchStatus.PENDING;
        d.attemptCount = 0;
        Instant now = Instant.now();
        d.createdAt = now;
        d.updatedAt = now;
        return d;
    }

    /** 성공 기록. */
    public void markSuccess(String title, String body, TemplateSource source) {
        this.status = DispatchStatus.SUCCESS;
        Instant now = Instant.now();
        this.sentAt = now;
        this.generatedTitle = title;
        this.generatedBody = body;
        this.templateSource = source;
        this.lastError = null;
        this.updatedAt = now;
    }

    /**
     * 실패 기록. title/body/source를 함께 저장하여 재시도 스케줄러가 재사용할 수 있게 한다.
     * last_notified_at은 갱신되지 않으므로 다음 배치가 자동 재시도한다.
     * DEAD 상태로 전환하지 않는다 — {@link #markDead(String)} 를 사용하라.
     */
    public void markFailed(String error, String title, String body, TemplateSource source) {
        this.status = DispatchStatus.FAILED;
        this.lastError = error;
        this.generatedTitle = title;
        this.generatedBody = body;
        this.templateSource = source;
        this.updatedAt = Instant.now();
    }

    /**
     * 재시도 한도 초과 시 DEAD로 전환한다.
     * last_notified_at은 갱신하지 않는다.
     */
    public void markDead(String error) {
        this.status = DispatchStatus.DEAD;
        this.lastError = error;
        this.updatedAt = Instant.now();
    }

    /** attempt_count를 1 증가시킨다. */
    public void incrementAttemptCount() {
        this.attemptCount++;
    }

    /**
     * attempt_count가 최대 재시도 횟수(5)에 도달했으면 true.
     * {@link #incrementAttemptCount()} 호출 후 이 메서드로 DEAD 전환 여부를 판단한다.
     */
    public boolean isRetryExhausted() {
        return this.attemptCount >= MAX_ATTEMPTS;
    }

    public boolean isPending() {
        return this.status == DispatchStatus.PENDING;
    }
}
