package dev.jazzybyte.onseoul.domain.model;

import lombok.Getter;

import java.time.LocalDateTime;

@Getter
public class CollectionHistory {

    private Long id;
    private Long sourceId;
    private LocalDateTime collectedAt;
    private CollectionStatus status;
    private int totalFetched;
    private int newCount;
    private int updatedCount;
    private int deletedCount;
    private Integer durationMs;
    private String errorMessage;

    public static CollectionHistory create(Long sourceId) {
        CollectionHistory h = new CollectionHistory();
        h.sourceId = sourceId;
        h.status = CollectionStatus.FAILED;
        h.collectedAt = LocalDateTime.now();
        return h;
    }

    /** Reconstitute from persistence. */
    public CollectionHistory(Long id, Long sourceId, LocalDateTime collectedAt,
                             CollectionStatus status, int totalFetched, int newCount,
                             int updatedCount, int deletedCount, Integer durationMs,
                             String errorMessage) {
        this.id = id;
        this.sourceId = sourceId;
        this.collectedAt = collectedAt;
        this.status = status;
        this.totalFetched = totalFetched;
        this.newCount = newCount;
        this.updatedCount = updatedCount;
        this.deletedCount = deletedCount;
        this.durationMs = durationMs;
        this.errorMessage = errorMessage;
    }

    private CollectionHistory() {}

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

    private void validateNotFinished() {
        if (this.durationMs != null) {
            throw new IllegalStateException(
                    "이미 결과가 기록된 수집 이력입니다. id=" + this.id + ", status=" + this.status);
        }
    }
}
