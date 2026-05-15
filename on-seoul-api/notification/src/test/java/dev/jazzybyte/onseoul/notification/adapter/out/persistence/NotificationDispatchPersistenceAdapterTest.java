package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:notif-dispatch-test;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded",
        "spring.sql.init.schema-locations=classpath:jpa-test-schema.sql"
})
@Import({
        NotificationDispatchPersistenceAdapter.class,
        NotificationSubscriptionPersistenceAdapter.class,
        NotificationPersistenceMapper.class
})
class NotificationDispatchPersistenceAdapterTest {

    @Autowired
    private NotificationDispatchPersistenceAdapter dispatchAdapter;

    @Autowired
    private NotificationSubscriptionPersistenceAdapter subscriptionAdapter;

    private Long subscriptionId;

    @BeforeEach
    void setUp() {
        dev.jazzybyte.onseoul.notification.domain.NotificationSubscription sub =
                dev.jazzybyte.onseoul.notification.domain.NotificationSubscription.create(10L, "SVC-SETUP");
        subscriptionId = subscriptionAdapter.save(sub).getId();
    }

    @Test
    @DisplayName("saveIfAbsent() 최초 insert → 저장된 dispatch 반환")
    void saveIfAbsent_newDispatch_returnsPresent() {
        NotificationDispatch dispatch = NotificationDispatch.create(subscriptionId, 100L);

        Optional<NotificationDispatch> result = dispatchAdapter.saveIfAbsent(dispatch);

        assertThat(result).isPresent();
        assertThat(result.get().getId()).isNotNull().isPositive();
        assertThat(result.get().getStatus()).isEqualTo(DispatchStatus.PENDING);
    }

    @Test
    @DisplayName("saveIfAbsent() 중복 insert → empty 반환 (멱등성)")
    void saveIfAbsent_duplicateDispatch_returnsEmpty() {
        NotificationDispatch dispatch = NotificationDispatch.create(subscriptionId, 200L);
        dispatchAdapter.saveIfAbsent(dispatch);

        Optional<NotificationDispatch> second = dispatchAdapter.saveIfAbsent(
                NotificationDispatch.create(subscriptionId, 200L));

        assertThat(second).isEmpty();
    }

    @Test
    @DisplayName("save() markSuccess 후 저장 — SUCCESS 상태 유지")
    void save_afterMarkSuccess_persistsSuccess() {
        NotificationDispatch dispatch = NotificationDispatch.create(subscriptionId, 300L);
        NotificationDispatch saved = dispatchAdapter.saveIfAbsent(dispatch).orElseThrow();

        saved.markSuccess("제목", "본문", TemplateSource.AI);
        NotificationDispatch updated = dispatchAdapter.save(saved);

        assertThat(updated.getStatus()).isEqualTo(DispatchStatus.SUCCESS);
        assertThat(updated.getGeneratedTitle()).isEqualTo("제목");
        assertThat(updated.getTemplateSource()).isEqualTo(TemplateSource.AI);
        assertThat(updated.getSentAt()).isNotNull();
    }

    @Test
    @DisplayName("save() markFailed 후 저장 — FAILED 상태 유지")
    void save_afterMarkFailed_persistsFailed() {
        NotificationDispatch dispatch = NotificationDispatch.create(subscriptionId, 400L);
        NotificationDispatch saved = dispatchAdapter.saveIfAbsent(dispatch).orElseThrow();

        saved.markFailed("네트워크 오류", 5);
        NotificationDispatch updated = dispatchAdapter.save(saved);

        assertThat(updated.getStatus()).isEqualTo(DispatchStatus.FAILED);
        assertThat(updated.getAttemptCount()).isEqualTo((short) 1);
        assertThat(updated.getLastError()).isEqualTo("네트워크 오류");
    }

    @Test
    @DisplayName("loadRetryable() — PENDING dispatch 반환")
    void loadRetryable_pendingDispatch_returnsPresent() {
        NotificationDispatch dispatch = NotificationDispatch.create(subscriptionId, 500L);
        dispatchAdapter.saveIfAbsent(dispatch);

        Optional<NotificationDispatch> result =
                dispatchAdapter.loadRetryable(subscriptionId, 500L, 5);

        assertThat(result).isPresent();
        assertThat(result.get().getStatus()).isEqualTo(DispatchStatus.PENDING);
    }

    @Test
    @DisplayName("loadRetryable() — SUCCESS dispatch → empty 반환")
    void loadRetryable_successDispatch_returnsEmpty() {
        NotificationDispatch dispatch = NotificationDispatch.create(subscriptionId, 600L);
        NotificationDispatch saved = dispatchAdapter.saveIfAbsent(dispatch).orElseThrow();
        saved.markSuccess("제목", "본문", TemplateSource.FALLBACK);
        dispatchAdapter.save(saved);

        Optional<NotificationDispatch> result =
                dispatchAdapter.loadRetryable(subscriptionId, 600L, 5);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("loadRetryable() — attemptCount >= maxAttempts → empty 반환")
    void loadRetryable_attemptCountAtMax_returnsEmpty() {
        NotificationDispatch dispatch = NotificationDispatch.create(subscriptionId, 700L);
        NotificationDispatch saved = dispatchAdapter.saveIfAbsent(dispatch).orElseThrow();
        // 4번 실패 후 저장 (maxAttempts=5인 상황에서 attempt=4이면 아직 retryable)
        for (int i = 0; i < 4; i++) {
            saved.markFailed("오류", 5);
        }
        dispatchAdapter.save(saved);
        // attempt=4 → retryable
        assertThat(dispatchAdapter.loadRetryable(subscriptionId, 700L, 5)).isPresent();

        // 한번 더 실패 → attempt=5, DEAD
        saved.markFailed("마지막 오류", 5);
        dispatchAdapter.save(saved);

        Optional<NotificationDispatch> result =
                dispatchAdapter.loadRetryable(subscriptionId, 700L, 5);
        assertThat(result).isEmpty();
    }
}
