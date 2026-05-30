package dev.jazzybyte.onseoul.notification.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * NotificationBatch 도메인 단위 테스트 (ADR-0004).
 *
 * <p>start()/complete()/fail() 상태 전이만 검증한다. ID/타이밍은 어댑터가 채운다.
 */
class NotificationBatchTest {

    @Test
    @DisplayName("start() — RUNNING 상태로 초기화되고 startedAt이 채워진다")
    void start_initializesRunningAndStartedAt() {
        Instant before = Instant.now();
        NotificationBatch batch = NotificationBatch.start();

        assertThat(batch.getStatus()).isEqualTo(BatchStatus.RUNNING);
        assertThat(batch.getStartedAt()).isNotNull().isAfterOrEqualTo(before);
        assertThat(batch.getFinishedAt()).isNull();
        assertThat(batch.getSentCount()).isNull();
        assertThat(batch.getFailedCount()).isNull();
    }

    @Test
    @DisplayName("complete() — SUCCESS 상태로 전환, sent/failed 카운트와 finishedAt 채움")
    void complete_transitionsToSuccess() {
        NotificationBatch batch = NotificationBatch.start();

        batch.complete(7, 2);

        assertThat(batch.getStatus()).isEqualTo(BatchStatus.SUCCESS);
        assertThat(batch.getSentCount()).isEqualTo(7);
        assertThat(batch.getFailedCount()).isEqualTo(2);
        assertThat(batch.getFinishedAt()).isNotNull();
    }

    @Test
    @DisplayName("fail() — FAILED 상태로 전환, sent/failed 카운트와 finishedAt 채움")
    void fail_transitionsToFailed() {
        NotificationBatch batch = NotificationBatch.start();

        batch.fail(3, 5);

        assertThat(batch.getStatus()).isEqualTo(BatchStatus.FAILED);
        assertThat(batch.getSentCount()).isEqualTo(3);
        assertThat(batch.getFailedCount()).isEqualTo(5);
        assertThat(batch.getFinishedAt()).isNotNull();
    }

    @Test
    @DisplayName("reconstitute 생성자 — 영속화된 값을 그대로 보관")
    void reconstitute_preservesAllFields() {
        Instant started = Instant.parse("2026-05-22T09:00:00Z");
        Instant finished = Instant.parse("2026-05-22T09:05:00Z");
        NotificationBatch batch = new NotificationBatch(
                42L, started, finished, BatchStatus.SUCCESS, 10, 1);

        assertThat(batch.getId()).isEqualTo(42L);
        assertThat(batch.getStartedAt()).isEqualTo(started);
        assertThat(batch.getFinishedAt()).isEqualTo(finished);
        assertThat(batch.getStatus()).isEqualTo(BatchStatus.SUCCESS);
        assertThat(batch.getSentCount()).isEqualTo(10);
        assertThat(batch.getFailedCount()).isEqualTo(1);
    }
}
