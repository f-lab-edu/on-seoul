package dev.jazzybyte.onseoul.notification.domain;

import lombok.Getter;

import java.time.Instant;

/**
 * 알림 스케줄러 실행 단위 (ADR-0004).
 *
 * <p>id, startedAt은 INSERT 시 발급/기록되고,
 * finishedAt, status, sentCount, failedCount는 배치 종료 시 갱신된다.
 * collection_history와 동일한 배치 추적 패턴.
 */
@Getter
public class NotificationBatch {

    private Long id;
    private Instant startedAt;
    private Instant finishedAt;
    private BatchStatus status;
    private Integer sentCount;
    private Integer failedCount;

    /** Reconstitute from persistence. */
    public NotificationBatch(Long id, Instant startedAt, Instant finishedAt,
                             BatchStatus status, Integer sentCount, Integer failedCount) {
        this.id = id;
        this.startedAt = startedAt;
        this.finishedAt = finishedAt;
        this.status = status;
        this.sentCount = sentCount;
        this.failedCount = failedCount;
    }

    private NotificationBatch() {}

    /** Factory: 신규 RUNNING 배치 생성. id, startedAt은 저장 시 채워진다. */
    public static NotificationBatch start() {
        NotificationBatch b = new NotificationBatch();
        b.status = BatchStatus.RUNNING;
        b.startedAt = Instant.now();
        return b;
    }

    /** 정상 종료 처리: sent/failed 카운트를 기록한다. */
    public void complete(int sentCount, int failedCount) {
        this.status = BatchStatus.SUCCESS;
        this.sentCount = sentCount;
        this.failedCount = failedCount;
        this.finishedAt = Instant.now();
    }

    /** 비정상 종료 처리(배치 orchestration 자체 실패). */
    public void fail(int sentCount, int failedCount) {
        this.status = BatchStatus.FAILED;
        this.sentCount = sentCount;
        this.failedCount = failedCount;
        this.finishedAt = Instant.now();
    }
}
