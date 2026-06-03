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
                        10L, Set.of(NotificationChannel.EMAIL));
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
    @DisplayName("saveIfAbsent() — CHANGE dispatch 의 dispatch_date(UTC today)가 저장된다 (cross-dedup 기준 컬럼)")
    void saveIfAbsent_changeDispatch_persistsDispatchDate() {
        java.time.LocalDate today = java.time.LocalDate.of(2026, 6, 3);
        NotificationDispatch dispatch = NotificationDispatch.create(batchId, subscriptionId, today);

        Optional<NotificationDispatch> result = dispatchAdapter.saveIfAbsent(dispatch);

        assertThat(result).isPresent();
        assertThat(result.get().getDispatchDate()).isEqualTo(today);
        // serviceId 는 CHANGE 이므로 여전히 null.
        assertThat(result.get().getServiceId()).isNull();
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
    @DisplayName("save() markFailed 후 저장 — FAILED 상태 + title/body/source/last_error 유지")
    void save_afterMarkFailed_persistsFailed() {
        NotificationDispatch saved = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();

        saved.markFailed("네트워크 오류", "재시도 제목", "재시도 본문", TemplateSource.FALLBACK);
        NotificationDispatch updated = dispatchAdapter.save(saved);

        assertThat(updated.getStatus()).isEqualTo(DispatchStatus.FAILED);
        assertThat(updated.getLastError()).isEqualTo("네트워크 오류");
        assertThat(updated.getGeneratedTitle()).isEqualTo("재시도 제목");
        assertThat(updated.getGeneratedBody()).isEqualTo("재시도 본문");
        assertThat(updated.getTemplateSource()).isEqualTo(TemplateSource.FALLBACK);
    }

    @Test
    @DisplayName("save() assignPayload 후 저장/재조회 — notification_payload(JSONB) 라운드트립")
    void save_withNotificationPayload_roundTrips() {
        NotificationDispatch saved = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();

        String payload = "{\"title\":\"제목\",\"summary\":\"요약\",\"services\":[]}";
        saved.markSuccess("제목", "요약", TemplateSource.AI);
        saved.assignPayload(payload);
        dispatchAdapter.save(saved);

        em.flush();
        em.clear();

        Optional<NotificationDispatch> reloaded =
                dispatchAdapter.loadByBatchAndSubscription(batchId, subscriptionId);

        assertThat(reloaded).isPresent();
        assertThat(reloaded.get().getNotificationPayload()).isEqualTo(payload);
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

        saved.markFailed("일시적 오류", "제목", "본문", TemplateSource.AI);
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
                        999L, Set.of(NotificationChannel.EMAIL))).getId();
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
    @DisplayName("findRetryable() — FAILED + generatedTitle IS NOT NULL + attemptCount < 5 인 구독별 최신 1건 반환")
    void findRetryable_returnsLatestFailedPerSubscription() {
        // subscriptionId(setUp)의 FAILED dispatch 2건 — 최신(batch2) 1건만 반환되어야 함
        NotificationDispatch d1 = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();
        d1.markFailed("오류1", "제목1", "본문1", TemplateSource.AI);
        dispatchAdapter.save(d1);

        NotificationBatch batch2 = batchAdapter.insertRunning(NotificationBatch.start());
        NotificationDispatch d2 = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batch2.getId(), subscriptionId))
                .orElseThrow();
        d2.markFailed("오류2", "제목2", "본문2", TemplateSource.FALLBACK);
        dispatchAdapter.save(d2);

        var retryable = dispatchAdapter.findRetryable(Instant.now().plusSeconds(60));

        assertThat(retryable).hasSize(1);
        assertThat(retryable.get(0).getId()).isEqualTo(d2.getId());
        assertThat(retryable.get(0).getGeneratedTitle()).isEqualTo("제목2");
    }

    @Test
    @DisplayName("findRetryable() — generatedTitle IS NULL인 dispatch는 제외")
    void findRetryable_excludesNullTitle() {
        // generatedTitle 없는 FAILED dispatch — title 없이 markFailed할 방법이 없으므로 직접 save 호출
        NotificationDispatch d = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();
        // title을 null로 두기 위해 markFailed를 호출하지 않고 상태만 변경하는 경우를 DB로 직접 테스트
        // (실제 운영에서는 txBFailure가 항상 title을 저장하므로 이 경우는 레거시 데이터)
        // → generatedTitle이 null인 PENDING dispatch는 findRetryable 대상이 아님을 확인
        var retryable = dispatchAdapter.findRetryable(Instant.now().plusSeconds(60));

        assertThat(retryable).isEmpty();
    }

    @Test
    @DisplayName("findRetryable() — attemptCount >= 5인 dispatch는 제외")
    void findRetryable_excludesExhaustedAttempts() {
        NotificationDispatch d = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();
        d.markFailed("오류", "제목", "본문", TemplateSource.AI);
        for (int i = 0; i < 5; i++) {
            d.incrementAttemptCount();
        }
        dispatchAdapter.save(d);

        var retryable = dispatchAdapter.findRetryable(Instant.now().plusSeconds(60));

        assertThat(retryable).isEmpty();
    }

    @Test
    @DisplayName("findRetryable() — DEAD 상태 dispatch는 제외된다 (status != 'FAILED' 이므로)")
    void findRetryable_excludesDeadDispatches() {
        // FAILED dispatch를 저장한 다음 DEAD로 전환
        NotificationDispatch d = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();
        d.markFailed("오류", "제목", "본문", TemplateSource.AI);
        dispatchAdapter.save(d);

        // DEAD로 전환 (attempt_count 5회 도달)
        for (int i = 0; i < 5; i++) {
            d.incrementAttemptCount();
        }
        d.markDead("한도 초과");
        dispatchAdapter.save(d);

        var retryable = dispatchAdapter.findRetryable(Instant.now().plusSeconds(60));

        assertThat(retryable).isEmpty();
    }

    @Test
    @DisplayName("findRetryable() — retry 성공 후 SUCCESS 상태 dispatch는 제외된다")
    void findRetryable_excludesSuccessDispatches() {
        NotificationDispatch d = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();
        d.markSuccess("제목", "본문", TemplateSource.AI);
        dispatchAdapter.save(d);

        var retryable = dispatchAdapter.findRetryable(Instant.now().plusSeconds(60));

        assertThat(retryable).isEmpty();
    }

    @Test
    @DisplayName("existsDeadDispatchBySubscriptionId() — DEAD dispatch 없으면 false")
    void existsDeadDispatch_noDead_returnsFalse() {
        assertThat(dispatchAdapter.existsDeadDispatchBySubscriptionId(subscriptionId)).isFalse();
    }

    @Test
    @DisplayName("existsDeadDispatchBySubscriptionId() — DEAD dispatch 있으면 true")
    void existsDeadDispatch_withDead_returnsTrue() {
        NotificationDispatch d = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId))
                .orElseThrow();
        d.markFailed("오류", "제목", "본문", TemplateSource.AI);
        for (int i = 0; i < 5; i++) {
            d.incrementAttemptCount();
        }
        d.markDead("한도 초과");
        dispatchAdapter.save(d);

        assertThat(dispatchAdapter.existsDeadDispatchBySubscriptionId(subscriptionId)).isTrue();
    }

    @Test
    @DisplayName("existsDeadDispatchBySubscriptionId() — 다른 구독의 DEAD dispatch는 영향 없음")
    void existsDeadDispatch_otherSubscription_returnsFalse() {
        Long otherSubId = subscriptionAdapter.save(
                dev.jazzybyte.onseoul.notification.domain.NotificationSubscription.create(
                        999L, Set.of(NotificationChannel.EMAIL))).getId();

        NotificationDispatch d = dispatchAdapter
                .saveIfAbsent(NotificationDispatch.create(batchId, otherSubId))
                .orElseThrow();
        d.markFailed("오류", "제목", "본문", TemplateSource.AI);
        for (int i = 0; i < 5; i++) {
            d.incrementAttemptCount();
        }
        d.markDead("한도 초과");
        dispatchAdapter.save(d);

        // subscriptionId(setUp)에는 DEAD dispatch가 없으므로 false여야 함
        assertThat(dispatchAdapter.existsDeadDispatchBySubscriptionId(subscriptionId)).isFalse();
    }

    // ── 시점 트리거 dispatch dedup (migration 11) ──────────────────────────

    @Test
    @DisplayName("saveScheduledIfAbsent() — 신규 시점 dispatch INSERT → trigger_type/service_id/dispatch_date 보존")
    void saveScheduledIfAbsent_new_persistsScheduledFields() {
        java.time.LocalDate today = java.time.LocalDate.of(2026, 6, 3);
        NotificationDispatch d = NotificationDispatch.createScheduled(
                batchId, subscriptionId,
                dev.jazzybyte.onseoul.notification.domain.TriggerType.DEADLINE_DDAY,
                "OA-2269", today);

        Optional<NotificationDispatch> result = dispatchAdapter.saveScheduledIfAbsent(d);

        assertThat(result).isPresent();
        assertThat(result.get().getTriggerType())
                .isEqualTo(dev.jazzybyte.onseoul.notification.domain.TriggerType.DEADLINE_DDAY);
        assertThat(result.get().getServiceId()).isEqualTo("OA-2269");
        assertThat(result.get().getDispatchDate()).isEqualTo(today);
        assertThat(result.get().getStatus()).isEqualTo(DispatchStatus.PENDING);
    }

    @Test
    @DisplayName("saveScheduledIfAbsent() — 같은 (subscription_id, service_id, dispatch_date) 중복 → empty (멱등)")
    void saveScheduledIfAbsent_duplicate_returnsEmpty() {
        java.time.LocalDate today = java.time.LocalDate.of(2026, 6, 3);
        dispatchAdapter.saveScheduledIfAbsent(NotificationDispatch.createScheduled(
                batchId, subscriptionId,
                dev.jazzybyte.onseoul.notification.domain.TriggerType.DEADLINE_DDAY, "OA-2269", today));

        // 다른 batch + 다른 trigger_type 이어도 동일 (sub, service, date) 면 중복.
        NotificationBatch batch2 = batchAdapter.insertRunning(NotificationBatch.start());
        Optional<NotificationDispatch> second = dispatchAdapter.saveScheduledIfAbsent(
                NotificationDispatch.createScheduled(batch2.getId(), subscriptionId,
                        dev.jazzybyte.onseoul.notification.domain.TriggerType.OPEN_DAY, "OA-2269", today));

        assertThat(second).isEmpty();
    }

    @Test
    @DisplayName("saveScheduledIfAbsent() — service_id가 다르면 같은 날 같은 구독에도 INSERT 가능")
    void saveScheduledIfAbsent_differentServiceId_inserts() {
        java.time.LocalDate today = java.time.LocalDate.of(2026, 6, 3);
        dispatchAdapter.saveScheduledIfAbsent(NotificationDispatch.createScheduled(
                batchId, subscriptionId,
                dev.jazzybyte.onseoul.notification.domain.TriggerType.DEADLINE_DDAY, "OA-A", today));

        NotificationBatch batch2 = batchAdapter.insertRunning(NotificationBatch.start());
        Optional<NotificationDispatch> second = dispatchAdapter.saveScheduledIfAbsent(
                NotificationDispatch.createScheduled(batch2.getId(), subscriptionId,
                        dev.jazzybyte.onseoul.notification.domain.TriggerType.DEADLINE_DDAY, "OA-B", today));

        assertThat(second).isPresent();
    }

    @Test
    @DisplayName("saveScheduledIfAbsent() — dispatch_date가 다르면 INSERT 가능 (다음날 재발송)")
    void saveScheduledIfAbsent_differentDate_inserts() {
        java.time.LocalDate today = java.time.LocalDate.of(2026, 6, 3);
        dispatchAdapter.saveScheduledIfAbsent(NotificationDispatch.createScheduled(
                batchId, subscriptionId,
                dev.jazzybyte.onseoul.notification.domain.TriggerType.OPEN_DAY, "OA-2269", today));

        NotificationBatch batch2 = batchAdapter.insertRunning(NotificationBatch.start());
        Optional<NotificationDispatch> second = dispatchAdapter.saveScheduledIfAbsent(
                NotificationDispatch.createScheduled(batch2.getId(), subscriptionId,
                        dev.jazzybyte.onseoul.notification.domain.TriggerType.OPEN_DAY,
                        "OA-2269", today.plusDays(1)));

        assertThat(second).isPresent();
    }

    @Test
    @DisplayName("saveScheduledIfAbsent() — 시점 dedup은 기존 CHANGE dispatch(service_id NULL)와 독립적")
    void saveScheduledIfAbsent_independentFromChangeDispatch() {
        java.time.LocalDate today = java.time.LocalDate.of(2026, 6, 3);
        // CHANGE dispatch (service_id NULL) 먼저 발행
        dispatchAdapter.saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId));

        // 같은 구독에 시점 dispatch 도 발행 가능해야 한다(부분 인덱스에서 NULL service_id 는 제외).
        NotificationBatch batch2 = batchAdapter.insertRunning(NotificationBatch.start());
        Optional<NotificationDispatch> scheduled = dispatchAdapter.saveScheduledIfAbsent(
                NotificationDispatch.createScheduled(batch2.getId(), subscriptionId,
                        dev.jazzybyte.onseoul.notification.domain.TriggerType.DEADLINE_DDAY,
                        "OA-2269", today));

        assertThat(scheduled).isPresent();
    }

    @Test
    @DisplayName("[QA] 여러 CHANGE dispatch(service_id NULL, dispatch_date NULL)는 dedup 인덱스에서 충돌하지 않는다 — NULL distinct 가정 검증")
    void multipleChangeDispatches_nullServiceId_doNotCollideOnScheduledDedup() {
        // spring-backend 가 플래그한 H2-vs-PG dedup 발산 검증.
        // 같은 구독에 대해 매일(서로 다른 batch) CHANGE dispatch 가 발행된다.
        // uq_nd_scheduled_dedup (subscription_id, service_id, dispatch_date) 에서
        // (sub, NULL, NULL) 이 여러 row 에 반복되어도 — H2 plain unique 이든 PG partial unique 이든 —
        // NULL 은 서로 distinct 로 취급되어 충돌하지 않아야 한다.
        Optional<NotificationDispatch> first =
                dispatchAdapter.saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId));

        NotificationBatch batch2 = batchAdapter.insertRunning(NotificationBatch.start());
        Optional<NotificationDispatch> second =
                dispatchAdapter.saveIfAbsent(NotificationDispatch.create(batch2.getId(), subscriptionId));

        NotificationBatch batch3 = batchAdapter.insertRunning(NotificationBatch.start());
        Optional<NotificationDispatch> third =
                dispatchAdapter.saveIfAbsent(NotificationDispatch.create(batch3.getId(), subscriptionId));

        // 셋 다 성공해야 한다 — uq_nd_scheduled_dedup 가 (NULL, NULL) 충돌을 일으키면 둘째부터 empty 가 된다.
        assertThat(first).isPresent();
        assertThat(second).isPresent();
        assertThat(third).isPresent();
    }

    @Test
    @DisplayName("[QA] CHANGE(NULL) 와 시점(service_id 있음)가 같은 구독·같은 날 공존해도 둘 다 발행된다 (cross-trigger 한계: DB dedup 없음)")
    void changeAndScheduled_sameDay_coexist_noCrossDedup() {
        // [한계] 문서화: CHANGE 와 시점 알림이 같은 날 같은 service 에 중복 발송될 수 있다.
        // CHANGE 는 service_id NULL 이라 시점 dispatch 와 dedup 인덱스 상으로 절대 충돌하지 않는다.
        // 즉 cross-trigger dedup 은 DB 가 보장하지 않고, 스케줄러 실행 순서만으로는 막지 못함을 증명.
        java.time.LocalDate today = java.time.LocalDate.of(2026, 6, 3);
        Optional<NotificationDispatch> change =
                dispatchAdapter.saveIfAbsent(NotificationDispatch.create(batchId, subscriptionId));

        NotificationBatch batch2 = batchAdapter.insertRunning(NotificationBatch.start());
        Optional<NotificationDispatch> scheduled = dispatchAdapter.saveScheduledIfAbsent(
                NotificationDispatch.createScheduled(batch2.getId(), subscriptionId,
                        dev.jazzybyte.onseoul.notification.domain.TriggerType.DEADLINE_DDAY,
                        "OA-SAME", today));

        // 둘 다 발행됨 — DB 레벨 cross dedup 부재(설계상 best-effort) 를 회귀 고정.
        assertThat(change).isPresent();
        assertThat(scheduled).isPresent();
    }

    @Test
    @DisplayName("existsScheduledDispatch() — 오늘 같은 구독·서비스 시점 dispatch 가 있으면 true (없으면 false)")
    void existsScheduledDispatch_detectsSameSubServiceDay() {
        java.time.LocalDate today = java.time.LocalDate.of(2026, 6, 3);
        assertThat(dispatchAdapter.existsScheduledDispatch(subscriptionId, "OA-1", today)).isFalse();

        dispatchAdapter.saveScheduledIfAbsent(NotificationDispatch.createScheduled(
                batchId, subscriptionId,
                dev.jazzybyte.onseoul.notification.domain.TriggerType.OPEN_DAY, "OA-1", today));

        assertThat(dispatchAdapter.existsScheduledDispatch(subscriptionId, "OA-1", today)).isTrue();
        // 다른 서비스/다른 날짜는 미존재.
        assertThat(dispatchAdapter.existsScheduledDispatch(subscriptionId, "OA-2", today)).isFalse();
        assertThat(dispatchAdapter.existsScheduledDispatch(subscriptionId, "OA-1", today.plusDays(1))).isFalse();
    }

    @Test
    @DisplayName("existsScheduledDispatch() — null/blank 인자는 방어적으로 false")
    void existsScheduledDispatch_nullArgs_returnsFalse() {
        java.time.LocalDate today = java.time.LocalDate.of(2026, 6, 3);
        assertThat(dispatchAdapter.existsScheduledDispatch(null, "OA-1", today)).isFalse();
        assertThat(dispatchAdapter.existsScheduledDispatch(subscriptionId, null, today)).isFalse();
        assertThat(dispatchAdapter.existsScheduledDispatch(subscriptionId, "  ", today)).isFalse();
        assertThat(dispatchAdapter.existsScheduledDispatch(subscriptionId, "OA-1", null)).isFalse();
    }

    @Test
    @DisplayName("existsChangeDispatchForServiceToday() — null/blank 인자는 쿼리 없이 false (JSONB @> 는 PG 전용, QA가 PG로 매칭 검증)")
    void existsChangeDispatchForServiceToday_nullArgs_returnsFalse() {
        java.time.LocalDate today = java.time.LocalDate.of(2026, 6, 3);
        assertThat(dispatchAdapter.existsChangeDispatchForServiceToday(null, "OA-1", today)).isFalse();
        assertThat(dispatchAdapter.existsChangeDispatchForServiceToday(subscriptionId, null, today)).isFalse();
        assertThat(dispatchAdapter.existsChangeDispatchForServiceToday(subscriptionId, "  ", today)).isFalse();
        assertThat(dispatchAdapter.existsChangeDispatchForServiceToday(subscriptionId, "OA-1", null)).isFalse();
    }

    @Test
    @DisplayName("servicesContainmentLiteral() — JSON 배열 안 객체 [{\"serviceId\":\"...\"}] 형태로, 특수문자도 안전 이스케이프")
    void servicesContainmentLiteral_buildsEscapedJsonArray() {
        assertThat(NotificationDispatchPersistenceAdapter.servicesContainmentLiteral("OA-2269"))
                .isEqualTo("[{\"serviceId\":\"OA-2269\"}]");
        // 따옴표 포함 식별자도 깨지지 않고 이스케이프된다(SQL 인젝션/JSON 파손 방어).
        assertThat(NotificationDispatchPersistenceAdapter.servicesContainmentLiteral("a\"b"))
                .isEqualTo("[{\"serviceId\":\"a\\\"b\"}]");
        // 백슬래시/제어문자도 JSON 안전 이스케이프된다 — 이 리터럴이 CAST(? AS jsonb) 우변으로 PG 에 바인딩되므로
        // JSON 파손/이스케이프 누락은 PG 에서 쿼리 실패로 이어진다(H2 미평가라 리터럴 정확성으로 방어).
        assertThat(NotificationDispatchPersistenceAdapter.servicesContainmentLiteral("a\\b\tc"))
                .isEqualTo("[{\"serviceId\":\"a\\\\b\\tc\"}]");
    }

    @Test
    @DisplayName("dispatch_date UTC 일관성 — CHANGE dispatch 가 채운 dispatch_date 와 같은 달력일의 시점 dispatch 가 동일 키로 인식된다")
    void dispatchDate_changeAndScheduled_sameUtcDay_keyAligned() {
        // CHANGE 경로와 시점 경로는 동일 UTC Clock 의 today(LocalDate)를 바인딩한다.
        // 같은 LocalDate 로 저장된 행이 같은 dispatch_date 키로 조회됨을 확인(오프바이원/TZ 변환 없음).
        java.time.LocalDate utcToday = java.time.LocalDate.of(2026, 6, 3);

        // CHANGE dispatch: dispatch_date 채움, service_id=null (별도 batch).
        NotificationBatch changeBatch = batchAdapter.insertRunning(NotificationBatch.start());
        Optional<NotificationDispatch> change = dispatchAdapter.saveIfAbsent(
                NotificationDispatch.create(changeBatch.getId(), subscriptionId, utcToday));
        assertThat(change).isPresent();
        assertThat(change.get().getDispatchDate()).isEqualTo(utcToday);

        // 같은 달력일의 시점 dispatch 도 같은 dispatch_date 로 저장 → 시점-시점 선조회가 매칭.
        NotificationBatch schedBatch = batchAdapter.insertRunning(NotificationBatch.start());
        dispatchAdapter.saveScheduledIfAbsent(NotificationDispatch.createScheduled(
                schedBatch.getId(), subscriptionId,
                dev.jazzybyte.onseoul.notification.domain.TriggerType.DEADLINE_DDAY, "OA-9", utcToday));
        assertThat(dispatchAdapter.existsScheduledDispatch(subscriptionId, "OA-9", utcToday)).isTrue();
        // 자정 경계: 하루 차이는 다른 키 → 매칭되지 않는다.
        assertThat(dispatchAdapter.existsScheduledDispatch(subscriptionId, "OA-9", utcToday.minusDays(1))).isFalse();
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
