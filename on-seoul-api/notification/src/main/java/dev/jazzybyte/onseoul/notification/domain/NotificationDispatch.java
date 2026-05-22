package dev.jazzybyte.onseoul.notification.domain;

import lombok.Getter;

import java.time.Instant;

/**
 * 배치 × 구독 단위 알림 발송 이력 (ADR-0004).
 *
 * <p>per-change 모델에서 제거된 필드: {@code changeLogId}, {@code attemptCount}.
 * 추가된 필드: {@code batchId}.
 */
@Getter
public class NotificationDispatch {

    private Long id;
    private Long batchId;
    private Long subscriptionId;
    private DispatchStatus status;
    private Instant sentAt;
    private String generatedTitle;
    private String generatedBody;
    private TemplateSource templateSource;
    private String lastError;
    private Instant createdAt;
    private Instant updatedAt;

    /** Reconstitute from persistence. */
    public NotificationDispatch(Long id, Long batchId, Long subscriptionId,
                                DispatchStatus status,
                                Instant sentAt, String generatedTitle, String generatedBody,
                                TemplateSource templateSource, String lastError,
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
     * 실패 기록. last_notified_at은 갱신되지 않으므로 다음 배치가 자동 재시도한다.
     * DEAD 상태로 전환하지 않는다(ADR-0004).
     */
    public void markFailed(String error) {
        this.status = DispatchStatus.FAILED;
        this.lastError = error;
        this.updatedAt = Instant.now();
    }

    public boolean isPending() {
        return this.status == DispatchStatus.PENDING;
    }
}
