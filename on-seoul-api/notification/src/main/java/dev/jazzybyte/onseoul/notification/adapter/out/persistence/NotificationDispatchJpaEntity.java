package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;

import java.time.Instant;

@Entity
@Table(name = "notification_dispatches")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class NotificationDispatchJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "subscription_id", nullable = false)
    private Long subscriptionId;

    @Column(name = "change_log_id", nullable = false)
    private Long changeLogId;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 10)
    private DispatchStatus status;

    @Column(name = "attempt_count", nullable = false, columnDefinition = "SMALLINT")
    private short attemptCount;

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

    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    NotificationDispatchJpaEntity(Long subscriptionId, Long changeLogId) {
        this.subscriptionId = subscriptionId;
        this.changeLogId = changeLogId;
        this.status = DispatchStatus.PENDING;
        this.attemptCount = 0;
        this.updatedAt = Instant.now();
    }

    @PreUpdate
    void onPreUpdate() {
        this.updatedAt = Instant.now();
    }

    void applyDomain(DispatchStatus status, short attemptCount,
                     Instant sentAt, String generatedTitle, String generatedBody,
                     TemplateSource templateSource, String lastError) {
        this.status = status;
        this.attemptCount = attemptCount;
        this.sentAt = sentAt;
        this.generatedTitle = generatedTitle;
        this.generatedBody = generatedBody;
        this.templateSource = templateSource;
        this.lastError = lastError;
        this.updatedAt = Instant.now();
    }
}
