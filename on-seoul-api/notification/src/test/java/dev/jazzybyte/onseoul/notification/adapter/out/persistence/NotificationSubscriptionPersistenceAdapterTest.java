package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.time.Instant;
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
    @DisplayName("saveIfAbsent() — 구독을 INSERT 한다 (제약 위반 없이 정상 저장)")
    void saveIfAbsent_insertsRow() {
        NotificationSubscription sub = NotificationSubscription.create(99L,
                Set.of(NotificationChannel.EMAIL));
        adapter.saveIfAbsent(sub);

        List<NotificationSubscription> user99 = adapter.loadByUserId(99L);
        assertThat(user99).hasSize(1);
    }
}
