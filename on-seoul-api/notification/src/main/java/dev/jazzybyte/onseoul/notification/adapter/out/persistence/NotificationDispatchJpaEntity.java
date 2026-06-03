package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.domain.TriggerType;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.time.LocalDate;

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
    @Column(name = "trigger_type", nullable = false, length = 20)
    private TriggerType triggerType;

    @Column(name = "dispatch_date")
    private LocalDate dispatchDate;

    @Column(name = "service_id", length = 30)
    private String serviceId;

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
        this(batchId, subscriptionId, (LocalDate) null);
    }

    /**
     * CHANGE dispatch INSERT 용 — service_id 는 null 이지만 dispatch_date 를 채운다.
     * CHANGE↔시점 cross-trigger dedup 선조회의 "오늘 범위" 기준 컬럼(migration 12).
     */
    NotificationDispatchJpaEntity(Long batchId, Long subscriptionId, LocalDate dispatchDate) {
        this.batchId = batchId;
        this.subscriptionId = subscriptionId;
        this.triggerType = TriggerType.CHANGE;
        this.dispatchDate = dispatchDate;
        this.status = DispatchStatus.PENDING;
        this.attemptCount = 0;
        this.updatedAt = Instant.now();
    }

    /** 시점 트리거 dispatch INSERT 용 — service_id + dispatch_date 를 채운다. */
    NotificationDispatchJpaEntity(Long batchId, Long subscriptionId,
                                  TriggerType triggerType, String serviceId, LocalDate dispatchDate) {
        this.batchId = batchId;
        this.subscriptionId = subscriptionId;
        this.triggerType = triggerType;
        this.serviceId = serviceId;
        this.dispatchDate = dispatchDate;
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
