package dev.jazzybyte.onseoul.notification.domain;

import lombok.Getter;

import java.time.Instant;

@Getter
public class NotificationDispatch {

    private Long id;
    private Long subscriptionId;
    private Long changeLogId;
    private DispatchStatus status;
    private short attemptCount;
    private Instant sentAt;
    private String generatedTitle;
    private String generatedBody;
    private TemplateSource templateSource;
    private String lastError;
    private Instant createdAt;
    private Instant updatedAt;

    /** Reconstitute from persistence. */
    public NotificationDispatch(Long id, Long subscriptionId, Long changeLogId,
                                DispatchStatus status, short attemptCount,
                                Instant sentAt, String generatedTitle, String generatedBody,
                                TemplateSource templateSource, String lastError,
                                Instant createdAt, Instant updatedAt) {
        this.id = id;
        this.subscriptionId = subscriptionId;
        this.changeLogId = changeLogId;
        this.status = status;
        this.attemptCount = attemptCount;
        this.sentAt = sentAt;
        this.generatedTitle = generatedTitle;
        this.generatedBody = generatedBody;
        this.templateSource = templateSource;
        this.lastError = lastError;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    private NotificationDispatch() {}

    /** Factory method — creates a new PENDING dispatch. */
    public static NotificationDispatch create(Long subscriptionId, Long changeLogId) {
        NotificationDispatch d = new NotificationDispatch();
        d.subscriptionId = subscriptionId;
        d.changeLogId = changeLogId;
        d.status = DispatchStatus.PENDING;
        d.attemptCount = 0;
        Instant now = Instant.now();
        d.createdAt = now;
        d.updatedAt = now;
        return d;
    }

    /** Records a successful notification send. */
    public void markSuccess(String title, String body, TemplateSource source) {
        this.attemptCount++;
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
     * Records a failed attempt. Transitions to DEAD when attemptCount reaches maxAttempts.
     */
    public void markFailed(String error, int maxAttempts) {
        this.attemptCount++;
        this.lastError = error;
        this.status = this.attemptCount >= maxAttempts ? DispatchStatus.DEAD : DispatchStatus.FAILED;
        this.updatedAt = Instant.now();
    }

    public boolean isPending() {
        return this.status == DispatchStatus.PENDING;
    }

    public boolean isRetryable(int maxAttempts) {
        return (this.status == DispatchStatus.PENDING || this.status == DispatchStatus.FAILED)
                && this.attemptCount < maxAttempts;
    }
}
