package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.BatchStatus;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.Instant;

@Entity
@Table(name = "notification_batch")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class NotificationBatchJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "started_at", nullable = false)
    private Instant startedAt;

    @Column(name = "finished_at")
    private Instant finishedAt;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 20)
    private BatchStatus status;

    @Column(name = "sent_count")
    private Integer sentCount;

    @Column(name = "failed_count")
    private Integer failedCount;

    NotificationBatchJpaEntity(Instant startedAt, BatchStatus status) {
        this.startedAt = startedAt;
        this.status = status;
    }

    void apply(BatchStatus status, Instant finishedAt, Integer sentCount, Integer failedCount) {
        this.status = status;
        this.finishedAt = finishedAt;
        this.sentCount = sentCount;
        this.failedCount = failedCount;
    }
}
