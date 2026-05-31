package dev.jazzybyte.onseoul.notification.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class NotificationDispatchTest {

    @Test
    @DisplayName("create() — PENDING 상태로 초기화, batchId/subscriptionId 보관, attemptCount=0")
    void create_initializesAsPending() {
        NotificationDispatch dispatch = NotificationDispatch.create(7L, 10L);

        assertThat(dispatch.getBatchId()).isEqualTo(7L);
        assertThat(dispatch.getSubscriptionId()).isEqualTo(10L);
        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.PENDING);
        assertThat(dispatch.isPending()).isTrue();
        assertThat(dispatch.getAttemptCount()).isEqualTo(0);
    }

    @Test
    @DisplayName("markSuccess() — SUCCESS 상태로 전환, 메시지/sentAt 저장")
    void markSuccess_transitionsToSuccess() {
        NotificationDispatch dispatch = NotificationDispatch.create(7L, 10L);

        dispatch.markSuccess("제목", "본문", TemplateSource.AI);

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.SUCCESS);
        assertThat(dispatch.getGeneratedTitle()).isEqualTo("제목");
        assertThat(dispatch.getGeneratedBody()).isEqualTo("본문");
        assertThat(dispatch.getTemplateSource()).isEqualTo(TemplateSource.AI);
        assertThat(dispatch.getSentAt()).isNotNull();
        assertThat(dispatch.getLastError()).isNull();
    }

    @Test
    @DisplayName("markFailed() — FAILED 상태 + title/body/source/lastError 저장")
    void markFailed_storesTitleBodySourceAndError() {
        NotificationDispatch dispatch = NotificationDispatch.create(7L, 10L);

        dispatch.markFailed("발송 오류", "재시도 제목", "재시도 본문", TemplateSource.FALLBACK);

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.FAILED);
        assertThat(dispatch.getLastError()).isEqualTo("발송 오류");
        assertThat(dispatch.getGeneratedTitle()).isEqualTo("재시도 제목");
        assertThat(dispatch.getGeneratedBody()).isEqualTo("재시도 본문");
        assertThat(dispatch.getTemplateSource()).isEqualTo(TemplateSource.FALLBACK);
    }

    @Test
    @DisplayName("markFailed()를 여러 번 호출해도 상태는 FAILED 유지 (DEAD로 전환되지 않음)")
    void markFailed_repeatedly_remainsFailed() {
        NotificationDispatch dispatch = NotificationDispatch.create(7L, 10L);

        for (int i = 0; i < 10; i++) {
            dispatch.markFailed("오류 " + i, "제목", "본문", TemplateSource.AI);
        }

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.FAILED);
        assertThat(dispatch.getLastError()).isEqualTo("오류 9");
    }

    @Test
    @DisplayName("markDead() — DEAD 상태로 전환, lastError 저장")
    void markDead_transitionsToDead() {
        NotificationDispatch dispatch = NotificationDispatch.create(7L, 10L);

        dispatch.markDead("한도 초과");

        assertThat(dispatch.getStatus()).isEqualTo(DispatchStatus.DEAD);
        assertThat(dispatch.getLastError()).isEqualTo("한도 초과");
    }

    @Test
    @DisplayName("incrementAttemptCount() — attemptCount가 1씩 증가")
    void incrementAttemptCount_increments() {
        NotificationDispatch dispatch = NotificationDispatch.create(7L, 10L);
        assertThat(dispatch.getAttemptCount()).isEqualTo(0);

        dispatch.incrementAttemptCount();
        assertThat(dispatch.getAttemptCount()).isEqualTo(1);

        dispatch.incrementAttemptCount();
        assertThat(dispatch.getAttemptCount()).isEqualTo(2);
    }

    @Test
    @DisplayName("isRetryExhausted() — attemptCount=4이면 false, 5이면 true")
    void isRetryExhausted_boundaryValues() {
        NotificationDispatch dispatch = NotificationDispatch.create(7L, 10L);

        for (int i = 0; i < 4; i++) {
            dispatch.incrementAttemptCount();
        }
        assertThat(dispatch.isRetryExhausted()).isFalse();

        dispatch.incrementAttemptCount();
        assertThat(dispatch.isRetryExhausted()).isTrue();
    }
}
