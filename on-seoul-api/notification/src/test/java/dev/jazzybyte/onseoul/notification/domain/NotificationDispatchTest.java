package dev.jazzybyte.onseoul.notification.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class NotificationDispatchTest {

    @Test
    @DisplayName("create() — PENDING 상태로 초기화")
    void create_initializesAsPending() {
        NotificationDispatch dispatch = NotificationDispatch.create(10L, 20L);

        assertThat(dispatch.getSubscriptionId()).isEqualTo(10L);
        assertThat(dispatch.getChangeLogId()).isEqualTo(20L);
        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.PENDING);
        assertThat(dispatch.getAttemptCount()).isEqualTo((short) 0);
        assertThat(dispatch.isPending()).isTrue();
    }

    @Test
    @DisplayName("markSuccess() — SUCCESS 상태로 전환, 메시지 저장")
    void markSuccess_transitionsToSuccess() {
        NotificationDispatch dispatch = NotificationDispatch.create(10L, 20L);

        dispatch.markSuccess("제목", "본문", TemplateSource.AI);

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.SUCCESS);
        assertThat(dispatch.getAttemptCount()).isEqualTo((short) 1);
        assertThat(dispatch.getGeneratedTitle()).isEqualTo("제목");
        assertThat(dispatch.getGeneratedBody()).isEqualTo("본문");
        assertThat(dispatch.getTemplateSource()).isEqualTo(TemplateSource.AI);
        assertThat(dispatch.getSentAt()).isNotNull();
        assertThat(dispatch.getLastError()).isNull();
    }

    @Test
    @DisplayName("markFailed() — maxAttempts 미만이면 FAILED 상태")
    void markFailed_belowMaxAttempts_transitionsToFailed() {
        NotificationDispatch dispatch = NotificationDispatch.create(10L, 20L);

        dispatch.markFailed("FCM 오류", 5);

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.FAILED);
        assertThat(dispatch.getAttemptCount()).isEqualTo((short) 1);
        assertThat(dispatch.getLastError()).isEqualTo("FCM 오류");
    }

    @Test
    @DisplayName("markFailed() — attemptCount가 maxAttempts에 도달하면 DEAD 상태")
    void markFailed_atMaxAttempts_transitionsToDead() {
        NotificationDispatch dispatch = NotificationDispatch.create(10L, 20L);

        for (int i = 0; i < 4; i++) {
            dispatch.markFailed("오류", 5);
        }
        dispatch.markFailed("마지막 오류", 5);

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.DEAD);
        assertThat(dispatch.getAttemptCount()).isEqualTo((short) 5);
    }

    @Test
    @DisplayName("isRetryable() — PENDING이고 시도 횟수 미만이면 재시도 가능")
    void isRetryable_pendingBelowMax_returnsTrue() {
        NotificationDispatch dispatch = NotificationDispatch.create(10L, 20L);

        assertThat(dispatch.isRetryable(5)).isTrue();
    }

    @Test
    @DisplayName("isRetryable() — DEAD 상태면 재시도 불가")
    void isRetryable_dead_returnsFalse() {
        NotificationDispatch dispatch = NotificationDispatch.create(10L, 20L);
        for (int i = 0; i < 5; i++) {
            dispatch.markFailed("오류", 5);
        }

        assertThat(dispatch.isRetryable(5)).isFalse();
    }

    @Test
    @DisplayName("isRetryable() — SUCCESS 상태면 재시도 불가")
    void isRetryable_success_returnsFalse() {
        NotificationDispatch dispatch = NotificationDispatch.create(10L, 20L);
        dispatch.markSuccess("제목", "본문", TemplateSource.FALLBACK);

        assertThat(dispatch.isRetryable(5)).isFalse();
    }
}
