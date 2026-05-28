package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.BatchStatus;
import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;
import java.util.Optional;
import java.util.Set;

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
        NotificationBatchPersistenceAdapter.class,
        NotificationPersistenceMapper.class
})
class NotificationDispatchPersistenceAdapterTest {

    @Autowired private NotificationDispatchPersistenceAdapter dispatchAdapter;
    @Autowired private NotificationSubscriptionPersistenceAdapter subscriptionAdapter;
    @Autowired private NotificationBatchPersistenceAdapter batchAdapter;
    @Autowired private EntityManager em;

    private Long subscriptionId;
    private Long batchId;

    @BeforeEach
    void setUp() {
        dev.jazzybyte.onseoul.notification.domain.NotificationSubscription sub =
                dev.jazzybyte.onseoul.notification.domain.NotificationSubscription.create(
                        10L, "SVC-SETUP", Set.of(NotificationChannel.EMAIL));
        subscriptionId = subscriptionAdapter.save(sub).getId();

        NotificationBatch batch = batchAdapter.insertRunning(NotificationBatch.start());
        batchId = batch.getId();
    }

    @Test
    @DisplayName("saveIfAbsent() 최초 insert → 저장된 PENDING dispatch 반환")
    void saveIfAbsent_newDispatch_returnsPresent() {
        NotificationDispatch dispatch = NotificationDispatch.create(batchId, subscriptionId);

        Optional<NotificationDispatch> result = dispatchAdapter.saveIfAbsent(dispatch);

        assertThat(result).isPresent();
        assertThat(result.get().getId()).isNotNull().isPositive();
        assertThat(result.get().getStatus()).isEqualTo(DispatchStatus.PENDING);
        assertThat(result.get().getBatchId()).isEqualTo(batchId);
        assertThat(result.get().getSubscriptionId()).isEqualTo(subscriptionId);
    }

    @Test
    @DisplayName("saveIfAbsent() 동일 (batch_id, subscription_id) 중복 → empty 반환 (멱등성)")
    void saveIfAbsent_duplicateDispatch_returnsEmpty() {
        dispatchAdapter.saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId));

        Optional<NotificationDispatch> second = dispatchAdapter.saveIfAbsent(
                NotificationDispatch.create(batchId, subscriptionId));

        assertThat(second).isEmpty();
    }

    @Test
    @DisplayName("다른 batch_id로는 같은 subscription_id에 INSERT 가능하다")
    void saveIfAbsent_differentBatchId_inserts() {
        dispatchAdapter.saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId));

        NotificationBatch batch2 = batchAdapter.insertRunning(NotificationBatch.start());
        Optional<NotificationDispatch> second = dispatchAdapter.saveIfAbsent(
                NotificationDispatch.create(batch2.getId(), subscriptionId));

        assertThat(second).isPresent();
    }

    @Test
    @DisplayName("save() markSuccess 후 저장 — SUCCESS 상태 유지")
    void save_afterMarkSuccess_persistsSuccess() {
        NotificationDispatch saved = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();

        saved.markSuccess("제목", "본문", TemplateSource.AI);
        NotificationDispatch updated = dispatchAdapter.save(saved);

        assertThat(updated.getStatus()).isEqualTo(DispatchStatus.SUCCESS);
        assertThat(updated.getGeneratedTitle()).isEqualTo("제목");
        assertThat(updated.getTemplateSource()).isEqualTo(TemplateSource.AI);
        assertThat(updated.getSentAt()).isNotNull();
    }

    @Test
    @DisplayName("save() markFailed 후 저장 — FAILED 상태 + last_error 유지")
    void save_afterMarkFailed_persistsFailed() {
        NotificationDispatch saved = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();

        saved.markFailed("네트워크 오류");
        NotificationDispatch updated = dispatchAdapter.save(saved);

        assertThat(updated.getStatus()).isEqualTo(DispatchStatus.FAILED);
        assertThat(updated.getLastError()).isEqualTo("네트워크 오류");
    }

    @Test
    @DisplayName("loadByBatchAndSubscription() — 저장된 dispatch 반환")
    void loadByBatchAndSubscription_returnsPresent() {
        dispatchAdapter.saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId));

        Optional<NotificationDispatch> result =
                dispatchAdapter.loadByBatchAndSubscription(batchId, subscriptionId);

        assertThat(result).isPresent();
        assertThat(result.get().getStatus()).isEqualTo(DispatchStatus.PENDING);
    }

    @Test
    @DisplayName("loadByBatchAndSubscription() — 없으면 empty 반환")
    void loadByBatchAndSubscription_missing_returnsEmpty() {
        Optional<NotificationDispatch> result =
                dispatchAdapter.loadByBatchAndSubscription(batchId, 99999L);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("save() 후 재저장 시 @PreUpdate가 updated_at을 갱신")
    void save_preUpdateRefreshesUpdatedAt() throws InterruptedException {
        NotificationDispatch saved = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();
        Instant updatedAtBefore = saved.getUpdatedAt();

        Thread.sleep(2);

        saved.markFailed("일시적 오류");
        NotificationDispatch updated = dispatchAdapter.save(saved);

        assertThat(updated.getUpdatedAt())
                .isNotNull()
                .isAfterOrEqualTo(updatedAtBefore);
    }

    @Test
    @DisplayName("loadByUserId() — 다른 유저의 dispatch 는 제외, id DESC 정렬")
    void loadByUserId_filtersByOwnership_andSortsDesc() {
        // user 10 (setUp 의 subscriptionId) 의 dispatch 2건
        NotificationDispatch d1 = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();
        NotificationBatch batch2 = batchAdapter.insertRunning(NotificationBatch.start());
        NotificationDispatch d2 = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batch2.getId(), subscriptionId))
                .orElseThrow();

        // 다른 유저 (userId=999) 소유 subscription + dispatch
        Long otherSubId = subscriptionAdapter.save(
                dev.jazzybyte.onseoul.notification.domain.NotificationSubscription.create(
                        999L, "SVC-OTHER", Set.of(NotificationChannel.EMAIL))).getId();
        dispatchAdapter.saveIfAbsent(NotificationDispatch.create(batchId, otherSubId));

        var list = dispatchAdapter.loadByUserId(10L, null, 50);

        assertThat(list).extracting(NotificationDispatch::getSubscriptionId)
                .containsOnly(subscriptionId);
        // id DESC: 마지막에 만든 d2 가 먼저
        assertThat(list.get(0).getId()).isEqualTo(d2.getId());
        assertThat(list.get(1).getId()).isEqualTo(d1.getId());
    }

    @Test
    @DisplayName("loadByUserId() — cursor 적용 시 id < cursor 만 반환")
    void loadByUserId_withCursor_returnsOnlyOlder() {
        NotificationDispatch d1 = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();
        NotificationBatch batch2 = batchAdapter.insertRunning(NotificationBatch.start());
        NotificationDispatch d2 = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batch2.getId(), subscriptionId))
                .orElseThrow();

        var list = dispatchAdapter.loadByUserId(10L, d2.getId(), 50);

        assertThat(list).extracting(NotificationDispatch::getId)
                .containsExactly(d1.getId());
    }

    @Test
    @DisplayName("loadByUserId() — limit 가 적용된다")
    void loadByUserId_respectsLimit() {
        dispatchAdapter.saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId));
        NotificationBatch batch2 = batchAdapter.insertRunning(NotificationBatch.start());
        dispatchAdapter.saveIfAbsent(NotificationDispatch.create(batch2.getId(), subscriptionId));
        NotificationBatch batch3 = batchAdapter.insertRunning(NotificationBatch.start());
        dispatchAdapter.saveIfAbsent(NotificationDispatch.create(batch3.getId(), subscriptionId));

        var list = dispatchAdapter.loadByUserId(10L, null, 2);

        assertThat(list).hasSize(2);
    }

    @Test
    @DisplayName("saveIfAbsent() — EntityManager 직접 insert 후 saveIfAbsent → DataIntegrityViolationException 경로로 empty")
    void saveIfAbsent_directDuplicateInsert_returnsEmpty() {
        NotificationDispatchJpaEntity duplicate =
                new NotificationDispatchJpaEntity(batchId, subscriptionId);
        em.persist(duplicate);
        em.flush();

        Optional<NotificationDispatch> result = dispatchAdapter.saveIfAbsent(
                NotificationDispatch.create(batchId, subscriptionId));

        assertThat(result).isEmpty();
    }
}
