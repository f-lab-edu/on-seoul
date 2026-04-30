package dev.jazzybyte.onseoul.domain.model;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class CollectionHistoryTest {

    @Test
    @DisplayName("create() — 초기 status가 FAILED이다")
    void create_initialStatus_isFailed() {
        CollectionHistory history = CollectionHistory.create(1L);

        assertThat(history.getStatus()).isEqualTo(CollectionStatus.FAILED);
    }

    @Test
    @DisplayName("create() — 초기 durationMs가 null이다 (미완료 상태)")
    void create_durationMs_isNull() {
        CollectionHistory history = CollectionHistory.create(1L);

        assertThat(history.getDurationMs()).isNull();
    }

    @Test
    @DisplayName("complete() — status=SUCCESS로 바뀌고 카운트 값들이 설정된다")
    void complete_setsSuccessAndCounts() {
        CollectionHistory history = CollectionHistory.create(1L);

        history.complete(100, 10, 5, 2, 3000);

        assertThat(history.getStatus()).isEqualTo(CollectionStatus.SUCCESS);
        assertThat(history.getTotalFetched()).isEqualTo(100);
        assertThat(history.getNewCount()).isEqualTo(10);
        assertThat(history.getUpdatedCount()).isEqualTo(5);
        assertThat(history.getDeletedCount()).isEqualTo(2);
        assertThat(history.getDurationMs()).isEqualTo(3000);
    }

    @Test
    @DisplayName("fail() — status=FAILED로 바뀌고 errorMessage와 durationMs가 설정된다")
    void fail_setsFailedStatusAndMessage() {
        CollectionHistory history = CollectionHistory.create(1L);

        history.fail("서울 API 호출 실패", 1500);

        assertThat(history.getStatus()).isEqualTo(CollectionStatus.FAILED);
        assertThat(history.getErrorMessage()).isEqualTo("서울 API 호출 실패");
        assertThat(history.getDurationMs()).isEqualTo(1500);
    }

    @Test
    @DisplayName("partial() — status=PARTIAL로 바뀌고 카운트와 errorMessage 모두 설정된다")
    void partial_setsPartialStatusAndBothCountsAndMessage() {
        CollectionHistory history = CollectionHistory.create(1L);

        history.partial(50, 8, 3, 1, 2000, "일부 페이지 수집 실패");

        assertThat(history.getStatus()).isEqualTo(CollectionStatus.PARTIAL);
        assertThat(history.getTotalFetched()).isEqualTo(50);
        assertThat(history.getNewCount()).isEqualTo(8);
        assertThat(history.getUpdatedCount()).isEqualTo(3);
        assertThat(history.getDeletedCount()).isEqualTo(1);
        assertThat(history.getDurationMs()).isEqualTo(2000);
        assertThat(history.getErrorMessage()).isEqualTo("일부 페이지 수집 실패");
    }

    @Test
    @DisplayName("complete() 두 번 호출 — IllegalStateException을 던진다")
    void complete_calledTwice_throwsIllegalStateException() {
        CollectionHistory history = CollectionHistory.create(1L);
        history.complete(100, 10, 5, 2, 3000);

        assertThatThrownBy(() -> history.complete(200, 20, 10, 4, 6000))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    @DisplayName("complete() 후 fail() 호출 — IllegalStateException을 던진다")
    void fail_afterComplete_throwsIllegalStateException() {
        CollectionHistory history = CollectionHistory.create(1L);
        history.complete(100, 10, 5, 2, 3000);

        assertThatThrownBy(() -> history.fail("에러", 500))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    @DisplayName("fail() 후 partial() 호출 — IllegalStateException을 던진다")
    void partial_afterFail_throwsIllegalStateException() {
        CollectionHistory history = CollectionHistory.create(1L);
        history.fail("초기 실패", 500);

        assertThatThrownBy(() -> history.partial(30, 5, 2, 0, 1000, "부분 실패"))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    @DisplayName("전체 arg 생성자(재구성용) — 모든 필드 값이 그대로 보존된다")
    void reconstitute_allFields_preserved() {
        LocalDateTime collectedAt = LocalDateTime.of(2026, 4, 30, 10, 0);

        CollectionHistory history = new CollectionHistory(
                42L, 7L, collectedAt,
                CollectionStatus.SUCCESS, 200, 30, 15, 5, 4500,
                null
        );

        assertThat(history.getId()).isEqualTo(42L);
        assertThat(history.getSourceId()).isEqualTo(7L);
        assertThat(history.getCollectedAt()).isEqualTo(collectedAt);
        assertThat(history.getStatus()).isEqualTo(CollectionStatus.SUCCESS);
        assertThat(history.getTotalFetched()).isEqualTo(200);
        assertThat(history.getNewCount()).isEqualTo(30);
        assertThat(history.getUpdatedCount()).isEqualTo(15);
        assertThat(history.getDeletedCount()).isEqualTo(5);
        assertThat(history.getDurationMs()).isEqualTo(4500);
        assertThat(history.getErrorMessage()).isNull();
    }
}
