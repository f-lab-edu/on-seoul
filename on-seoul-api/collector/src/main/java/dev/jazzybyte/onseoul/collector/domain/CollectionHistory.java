package dev.jazzybyte.onseoul.collector.domain;

import dev.jazzybyte.onseoul.collector.enums.CollectionStatus;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDateTime;

@Entity
@Table(name = "collection_history")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class CollectionHistory {

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

    @Builder
    public CollectionHistory(Long sourceId) {
        this.sourceId = sourceId;
        this.status = CollectionStatus.FAILED;
    }

    public void complete(int totalFetched, int newCount, int updatedCount, int deletedCount,
                         int durationMs) {
        validateNotFinished();
        this.status = CollectionStatus.SUCCESS;
        this.totalFetched = totalFetched;
        this.newCount = newCount;
        this.updatedCount = updatedCount;
        this.deletedCount = deletedCount;
        this.durationMs = durationMs;
    }

    public void fail(String errorMessage, int durationMs) {
        validateNotFinished();
        this.status = CollectionStatus.FAILED;
        this.errorMessage = errorMessage;
        this.durationMs = durationMs;
    }

    public void partial(int totalFetched, int newCount, int updatedCount, int deletedCount,
                        int durationMs, String errorMessage) {
        validateNotFinished();
        this.status = CollectionStatus.PARTIAL;
        this.totalFetched = totalFetched;
        this.newCount = newCount;
        this.updatedCount = updatedCount;
        this.deletedCount = deletedCount;
        this.durationMs = durationMs;
        this.errorMessage = errorMessage;
    }

    /**
     * 이미 결과가 기록된 이력에 중복으로 전이를 시도할 경우 예외를 던진다.
     * durationMs는 complete/fail/partial 세 메서드 모두에서 반드시 설정되므로,
     * null 여부로 "미완료" 상태를 판별한다.
     */
    private void validateNotFinished() {
        if (this.durationMs != null) {
            throw new IllegalStateException(
                    "이미 결과가 기록된 수집 이력입니다. id=" + this.id + ", status=" + this.status);
        }
    }
}
