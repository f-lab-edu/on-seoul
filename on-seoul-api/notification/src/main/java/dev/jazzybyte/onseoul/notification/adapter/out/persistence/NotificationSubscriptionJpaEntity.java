package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

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

    /**
     * JSONB in PostgreSQL; H2 test schema declares this as VARCHAR(2000).
     * Stored as a raw JSON string — VO mapping deferred to Phase 3.
     *
     * @JdbcTypeCode(SqlTypes.JSON) 은 Hibernate 6 에서 JDBC 바인딩 타입을 JSON 으로
     * 명시적으로 지정한다. columnDefinition = "jsonb" 만으로는 DDL 힌트에 그칠 뿐
     * JDBC 바인딩 타입은 여전히 character varying 으로 처리되어 PostgreSQL 이 거부한다.
     */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "filter", nullable = false, columnDefinition = "jsonb")
    private String filter;

    /**
     * 발송 채널 목록. JSONB in PostgreSQL; H2 test schema declares this as VARCHAR(500).
     * Stored as a JSON array string, e.g. ["EMAIL"] or ["EMAIL","SMS"].
     *
     * @see #filter 주석 — 동일한 이유로 @JdbcTypeCode(SqlTypes.JSON) 이 필요하다.
     */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "channels", nullable = false, columnDefinition = "jsonb")
    private String channels;

    @Column(name = "last_notified_at")
    private Instant lastNotifiedAt;

    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    NotificationSubscriptionJpaEntity(Long userId, String filter, String channels) {
        this.userId = userId;
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
