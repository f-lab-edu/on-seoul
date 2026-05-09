package dev.jazzybyte.onseoul.adapter.out.persistence.collection;

import dev.jazzybyte.onseoul.domain.model.CollectionStatus;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "collection_history")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
class CollectionHistoryJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "source_id", nullable = false)
    private Long sourceId;

    @CreationTimestamp
    @Column(name = "collected_at", nullable = false, updatable = false)
    private LocalDateTime collectedAt;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 10)
    private CollectionStatus status;

    @Column(name = "total_fetched", nullable = false)
    private int totalFetched;

    @Column(name = "new_count", nullable = false)
    private int newCount;

    @Column(name = "updated_count", nullable = false)
    private int updatedCount;

    @Column(name = "deleted_count", nullable = false)
    private int deletedCount;

    @Column(name = "duration_ms")
    private Integer durationMs;

    @Column(name = "error_message", columnDefinition = "TEXT")
    private String errorMessage;

    CollectionHistoryJpaEntity(Long sourceId, CollectionStatus status) {
        this.sourceId = sourceId;
        this.status = status;
    }

    void update(CollectionStatus status, int totalFetched, int newCount, int updatedCount,
                int deletedCount, Integer durationMs, String errorMessage) {
        this.status = status;
        this.totalFetched = totalFetched;
        this.newCount = newCount;
        this.updatedCount = updatedCount;
        this.deletedCount = deletedCount;
        this.durationMs = durationMs;
        this.errorMessage = errorMessage;
    }
}
