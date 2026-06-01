package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;

@Entity
@Table(name = "notification_dispatches")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class NotificationDispatchJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "batch_id", nullable = false)
    private Long batchId;

    @Column(name = "subscription_id", nullable = false)
    private Long subscriptionId;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 10)
    private DispatchStatus status;

    @Column(name = "sent_at")
    private Instant sentAt;

    @Column(name = "generated_title", length = 200)
    private String generatedTitle;

    @Column(name = "generated_body", columnDefinition = "TEXT")
    private String generatedBody;

    @Enumerated(EnumType.STRING)
    @Column(name = "template_source", length = 10)
    private TemplateSource templateSource;

    @Column(name = "last_error", columnDefinition = "TEXT")
    private String lastError;

    @Column(name = "attempt_count", nullable = false)
    private int attemptCount;

    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "notification_payload", columnDefinition = "jsonb")
    private String notificationPayload;  // raw JSON String (NotificationContent 직렬화)

    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    NotificationDispatchJpaEntity(Long batchId, Long subscriptionId) {
        this.batchId = batchId;
        this.subscriptionId = subscriptionId;
        this.status = DispatchStatus.PENDING;
        this.attemptCount = 0;
        this.updatedAt = Instant.now();
    }

    @PreUpdate
    void onPreUpdate() {
        this.updatedAt = Instant.now();
    }

    void applyDomain(DispatchStatus status,
                     Instant sentAt, String generatedTitle, String generatedBody,
                     TemplateSource templateSource, String lastError, int attemptCount,
                     String notificationPayload) {
        this.status = status;
        this.sentAt = sentAt;
        this.generatedTitle = generatedTitle;
        this.generatedBody = generatedBody;
        this.templateSource = templateSource;
        this.lastError = lastError;
        this.attemptCount = attemptCount;
        this.notificationPayload = notificationPayload;
        this.updatedAt = Instant.now();
    }
}
