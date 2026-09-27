package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.test.autoconfigure.orm.jpa.TestEntityManager;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:notif-sub-test;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded",
        "spring.sql.init.schema-locations=classpath:jpa-test-schema.sql"
})
@Import({NotificationSubscriptionPersistenceAdapter.class, NotificationPersistenceMapper.class})
class NotificationSubscriptionPersistenceAdapterTest {

    @Autowired
    private NotificationSubscriptionPersistenceAdapter adapter;

    @Autowired
    private TestEntityManager em;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Test
    @DisplayName("save() 신규 구독 → insert 후 id 채번")
    void save_newSubscription_insertsAndAssignsId() {
        NotificationSubscription sub = NotificationSubscription.create(1L,
                Set.of(NotificationChannel.EMAIL));

        NotificationSubscription saved = adapter.save(sub);

        assertThat(saved.getId()).isNotNull().isPositive();
        assertThat(saved.getUserId()).isEqualTo(1L);
        assertThat(saved.getFilter()).isEqualTo("{}");
        assertThat(saved.getChannels()).containsExactly(NotificationChannel.EMAIL);
        assertThat(saved.getLastNotifiedAt()).isNull();
    }

    @Test
    @DisplayName("save() EMAIL+SMS 복수 채널 → 저장 후 복원")
    void save_multipleChannels_persistsAndRestores() {
        NotificationSubscription sub = NotificationSubscription.create(3L,
                Set.of(NotificationChannel.EMAIL, NotificationChannel.SMS));

        NotificationSubscription saved = adapter.save(sub);

        assertThat(saved.getChannels()).containsExactlyInAnyOrder(
                NotificationChannel.EMAIL, NotificationChannel.SMS);
    }

    @Test
    @DisplayName("loadAll() — 저장된 구독 전체 반환")
    void loadAll_returnsAllSubscriptions() {
        adapter.save(NotificationSubscription.create(1L, Set.of(NotificationChannel.EMAIL)));
        adapter.save(NotificationSubscription.create(1L, Set.of(NotificationChannel.SMS)));

        List<NotificationSubscription> all = adapter.loadAll();

        assertThat(all).hasSizeGreaterThanOrEqualTo(2);
    }

    @Test
    @DisplayName("save() 기존 구독 — lastNotifiedAt 갱신")
    void save_existingSubscription_updatesLastNotifiedAt() {
        NotificationSubscription sub = NotificationSubscription.create(2L,
                Set.of(NotificationChannel.EMAIL));
        NotificationSubscription saved = adapter.save(sub);

        Instant now = Instant.now();
        saved.markNotified(now);
        NotificationSubscription updated = adapter.save(saved);

        assertThat(updated.getLastNotifiedAt()).isEqualTo(now);
    }

    @Test
    @DisplayName("loadByUserId() — 해당 유저의 구독만 반환, id ASC 정렬")
    void loadByUserId_returnsOnlyOwnSubscriptions() {
        adapter.save(NotificationSubscription.create(7L, Set.of(NotificationChannel.EMAIL)));
        adapter.save(NotificationSubscription.create(7L, Set.of(NotificationChannel.EMAIL)));
        adapter.save(NotificationSubscription.create(8L, Set.of(NotificationChannel.EMAIL)));

        List<NotificationSubscription> user7 = adapter.loadByUserId(7L);

        assertThat(user7).hasSize(2);
        assertThat(user7).extracting(NotificationSubscription::getUserId)
                .containsOnly(7L);
        assertThat(user7).extracting(NotificationSubscription::getId)
                .isSorted();
    }

    @Test
    @DisplayName("loadById() — 미존재 시 Optional.empty")
    void loadById_missing_returnsEmpty() {
        assertThat(adapter.loadById(999_999L)).isEmpty();
    }

    @Test
    @DisplayName("insert() — 동일 user_id로 여러 조건 기반 구독을 INSERT할 수 있다 (중복 제약 없음)")
    void insert_sameUserMultipleSubscriptions_allInserted() {
        NotificationSubscription first = NotificationSubscription.create(
                20L, Set.of(NotificationChannel.EMAIL));
        NotificationSubscription second = NotificationSubscription.create(
                20L, Set.of(NotificationChannel.SMS));

        NotificationSubscription savedFirst = adapter.insert(first);
        NotificationSubscription savedSecond = adapter.insert(second);

        assertThat(savedFirst.getId()).isNotEqualTo(savedSecond.getId());
        assertThat(adapter.loadByUserId(20L)).hasSize(2);
    }

    @Test
    @DisplayName("updatePartial() — filter 만 갱신, channels/lastNotifiedAt 보존")
    void updatePartial_filterOnly_preservesChannelsAndLastNotifiedAt() {
        NotificationSubscription created = adapter.save(NotificationSubscription.create(
                30L, Set.of(NotificationChannel.EMAIL, NotificationChannel.SMS)));
        Instant when = Instant.parse("2026-05-20T10:00:00Z");
        created.markNotified(when);
        adapter.save(created);

        NotificationSubscription after = adapter.updatePartial(
                created.getId(),
                new SubscriptionFilter(Set.of("RECEIVING"), null, null, null),
                null);

        assertThat(after.getFilter()).contains("RECEIVING");
        assertThat(after.getChannels()).containsExactlyInAnyOrder(
                NotificationChannel.EMAIL, NotificationChannel.SMS);
        assertThat(after.getLastNotifiedAt()).isEqualTo(when);
    }

    @Test
    @DisplayName("updatePartial() — channels 만 갱신, filter/lastNotifiedAt 보존")
    void updatePartial_channelsOnly_preservesFilterAndLastNotifiedAt() {
        NotificationSubscription created = adapter.save(NotificationSubscription.create(
                31L, Set.of(NotificationChannel.EMAIL)));
        Instant when = Instant.parse("2026-05-21T10:00:00Z");
        created.markNotified(when);
        adapter.save(created);

        NotificationSubscription after = adapter.updatePartial(
                created.getId(), null, Set.of(NotificationChannel.SMS));

        assertThat(after.getFilter()).isEqualTo("{}");
        assertThat(after.getChannels()).containsExactly(NotificationChannel.SMS);
        assertThat(after.getLastNotifiedAt()).isEqualTo(when);
    }

    @Test
    @DisplayName("deleteById() — row 삭제")
    void deleteById_removesRow() {
        NotificationSubscription created = adapter.save(NotificationSubscription.create(
                40L, Set.of(NotificationChannel.EMAIL)));

        adapter.deleteById(created.getId());

        assertThat(adapter.loadById(created.getId())).isEmpty();
    }

    @Test
    @DisplayName("deleteById() — 소프트 딜리트: 조회에서 빠지고 row 는 남는다")
    void 소프트삭제된_구독은_조회에서_빠지고_행은_남는다() {
        NotificationSubscription saved = adapter.insert(NotificationSubscription.create(
                41L, Set.of(NotificationChannel.EMAIL)));
        Long id = saved.getId();

        adapter.deleteById(id);
        em.flush();
        em.clear();   // 필수 — 영속성 컨텍스트 캐시가 있으면 DB 를 안 타고 그대로 반환된다

        assertThat(adapter.loadById(id)).isEmpty();                  // @SQLRestriction 이 by-id 로드에 적용됨
        assertThat(adapter.loadByUserId(saved.getUserId())).isEmpty();
        assertThat(jdbcTemplate.queryForObject(                      // @SQLDelete 동작 = 이력 보존
                "SELECT count(*) FROM notification_subscriptions WHERE id = ? AND deleted_at IS NOT NULL",
                Integer.class, id)).isEqualTo(1);
    }

    // ── 원 버그 재현: 발송 이력이 있는 구독 삭제 ─────────────────────────
    // H2 테스트 스키마도 notification_dispatches.subscription_id 를 ON DELETE 절 없는
    // FK(NO ACTION)로 선언하므로 프로덕션과 동일하게 재현된다. 하드 딜리트로 되돌리면
    // 여기서 DataIntegrityViolationException 이 터진다.

    @Test
    @DisplayName("deleteById() — 발송 이력이 있는 구독도 FK 위반 없이 삭제되고 이력은 보존된다")
    void 발송이력_있는_구독_삭제는_FK위반_없이_성공하고_이력을_보존한다() {
        NotificationSubscription saved = adapter.insert(NotificationSubscription.create(
                600L, Set.of(NotificationChannel.EMAIL)));
        Long id = saved.getId();

        jdbcTemplate.update("INSERT INTO notification_batch (status) VALUES ('SUCCESS')");
        Long batchId = jdbcTemplate.queryForObject(
                "SELECT max(id) FROM notification_batch", Long.class);
        jdbcTemplate.update(
                "INSERT INTO notification_dispatches (batch_id, subscription_id, status) VALUES (?, ?, 'SUCCESS')",
                batchId, id);

        adapter.deleteById(id);      // 하드 딜리트였다면 여기서 FK 위반
        em.flush();
        em.clear();

        assertThat(adapter.loadById(id)).isEmpty();
        assertThat(jdbcTemplate.queryForObject(   // 감사용 발송 이력 보존
                "SELECT count(*) FROM notification_dispatches WHERE subscription_id = ?",
                Integer.class, id)).isEqualTo(1);
    }

    // ── 소프트 딜리트 × 메인 배치 스케줄러 경로 (loadChunk) ──────────────
    // 여기가 새면 해지한 사용자에게 알림이 계속 발송된다. NotificationScheduler /
    // ScheduledTriggerScheduler 는 LoadSubscriptionPort 를 mock 하므로 필터링을
    // 검증할 수 있는 유일한 레벨이 이 어댑터 테스트다.

    @Test
    @DisplayName("loadChunk() — 소프트 삭제된 구독은 배치 발송 대상에서 제외된다")
    void 소프트삭제된_구독은_loadChunk_에서_제외된다() {
        Long keptId = adapter.insert(NotificationSubscription.create(
                510L, Set.of(NotificationChannel.EMAIL))).getId();
        Long deletedId = adapter.insert(NotificationSubscription.create(
                511L, Set.of(NotificationChannel.EMAIL))).getId();

        adapter.deleteById(deletedId);
        em.flush();
        em.clear();

        assertThat(adapter.loadChunk(0L, 100).stream().map(NotificationSubscription::getId))
                .contains(keptId)
                .doesNotContain(deletedId);
    }

    @Test
    @DisplayName("loadChunk() — keyset 경계에 걸린 소프트 삭제 행: 페이지가 짧아지거나 행을 건너뛰지 않는다")
    void 소프트삭제행이_섞인_keyset_순회는_활성구독을_모두_한번씩_방문한다() {
        List<Long> ids = new ArrayList<>();
        for (int i = 0; i < 6; i++) {
            ids.add(adapter.insert(NotificationSubscription.create(
                    520L + i, Set.of(NotificationChannel.EMAIL))).getId());
        }
        // 청크 크기 2 → 첫 페이지는 [0,1]. 두 번째 페이지 선두(index 2)를 삭제해
        // 경계 직후 행이 사라진 케이스를 만든다.
        adapter.deleteById(ids.get(2));
        em.flush();
        em.clear();

        List<Long> expected = List.of(ids.get(0), ids.get(1), ids.get(3), ids.get(4), ids.get(5));

        // NotificationScheduler.processAllSubscriptions() 의 순회 로직과 동일한 형태
        List<Long> visited = new ArrayList<>();
        long afterId = 0L;
        int chunkSize = 2;
        while (true) {
            List<NotificationSubscription> chunk = adapter.loadChunk(afterId, chunkSize);
            chunk.forEach(sub -> visited.add(sub.getId()));
            if (chunk.size() < chunkSize) {
                break; // 마지막 페이지
            }
            afterId = chunk.get(chunk.size() - 1).getId();
        }

        // 중복 없음 + 누락 없음 + 순서 유지. 삭제 행이 LIMIT 안에서 자리를 차지하면
        // 여기서 누락으로 잡힌다.
        assertThat(visited).containsExactlyElementsOf(expected);
    }

}
