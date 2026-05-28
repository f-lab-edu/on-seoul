package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;

import java.time.Instant;

@Entity
@Table(name = "notification_subscriptions")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class NotificationSubscriptionJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "user_id", nullable = false)
    private Long userId;

    @Column(name = "service_id", nullable = false, length = 30)
    private String serviceId;

    /**
     * JSONB in PostgreSQL; H2 test schema declares this as VARCHAR(2000).
     * Stored as a raw JSON string — VO mapping deferred to Phase 3.
     */
    @Column(name = "filter", nullable = false, columnDefinition = "jsonb")
    private String filter;

    /**
     * 발송 채널 목록. JSONB in PostgreSQL; H2 test schema declares this as VARCHAR(500).
     * Stored as a JSON array string, e.g. ["EMAIL"] or ["EMAIL","SMS"].
     */
    @Column(name = "channels", nullable = false, columnDefinition = "jsonb")
    private String channels;

    @Column(name = "last_notified_at")
    private Instant lastNotifiedAt;

    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    NotificationSubscriptionJpaEntity(Long userId, String serviceId, String filter, String channels) {
        this.userId = userId;
        this.serviceId = serviceId;
        this.filter = filter;
        this.channels = channels;
    }

    void updateLastNotifiedAt(Instant lastNotifiedAt) {
        this.lastNotifiedAt = lastNotifiedAt;
    }

    void updateFilter(String filter) {
        this.filter = filter;
    }

    void updateChannels(String channels) {
        this.channels = channels;
    }
}
