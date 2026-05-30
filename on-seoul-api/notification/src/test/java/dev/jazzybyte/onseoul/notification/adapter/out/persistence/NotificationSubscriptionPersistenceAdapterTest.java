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
        NotificationSubscription sub = NotificationSubscription.create(1L, "SVC-001",
                Set.of(NotificationChannel.EMAIL));

        NotificationSubscription saved = adapter.save(sub);

        assertThat(saved.getId()).isNotNull().isPositive();
        assertThat(saved.getUserId()).isEqualTo(1L);
        assertThat(saved.getServiceId()).isEqualTo("SVC-001");
        assertThat(saved.getFilter()).isEqualTo("{}");
        assertThat(saved.getChannels()).containsExactly(NotificationChannel.EMAIL);
        assertThat(saved.getLastNotifiedAt()).isNull();
    }

    @Test
    @DisplayName("save() EMAIL+SMS 복수 채널 → 저장 후 복원")
    void save_multipleChannels_persistsAndRestores() {
        NotificationSubscription sub = NotificationSubscription.create(3L, "SVC-010",
                Set.of(NotificationChannel.EMAIL, NotificationChannel.SMS));

        NotificationSubscription saved = adapter.save(sub);

        assertThat(saved.getChannels()).containsExactlyInAnyOrder(
                NotificationChannel.EMAIL, NotificationChannel.SMS);
    }

    @Test
    @DisplayName("loadAll() — 저장된 구독 전체 반환")
    void loadAll_returnsAllSubscriptions() {
        adapter.save(NotificationSubscription.create(1L, "SVC-001", Set.of(NotificationChannel.EMAIL)));
        adapter.save(NotificationSubscription.create(1L, "SVC-002", Set.of(NotificationChannel.SMS)));

        List<NotificationSubscription> all = adapter.loadAll();

        assertThat(all).hasSizeGreaterThanOrEqualTo(2);
    }

    @Test
    @DisplayName("save() 기존 구독 — lastNotifiedAt 갱신")
    void save_existingSubscription_updatesLastNotifiedAt() {
        NotificationSubscription sub = NotificationSubscription.create(2L, "SVC-003",
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
        adapter.save(NotificationSubscription.create(7L, "OA-A", Set.of(NotificationChannel.EMAIL)));
        adapter.save(NotificationSubscription.create(7L, "OA-B", Set.of(NotificationChannel.EMAIL)));
        adapter.save(NotificationSubscription.create(8L, "OA-C", Set.of(NotificationChannel.EMAIL)));

        List<NotificationSubscription> user7 = adapter.loadByUserId(7L);

        assertThat(user7).extracting(NotificationSubscription::getServiceId)
                .containsExactly("OA-A", "OA-B");
        assertThat(user7).extracting(NotificationSubscription::getUserId)
                .containsOnly(7L);
    }

    @Test
    @DisplayName("loadById() — 미존재 시 Optional.empty")
    void loadById_missing_returnsEmpty() {
        assertThat(adapter.loadById(999_999L)).isEmpty();
    }

    @Test
    @DisplayName("insert() — uq_ns_user_service 위반 시 DataIntegrityViolationException")
    void insert_duplicate_throwsDataIntegrityViolation() {
        NotificationSubscription first = NotificationSubscription.create(
                20L, "OA-DUP", Set.of(NotificationChannel.EMAIL));
        adapter.insert(first);

        NotificationSubscription dup = NotificationSubscription.create(
                20L, "OA-DUP", Set.of(NotificationChannel.EMAIL));

        org.assertj.core.api.Assertions.assertThatThrownBy(() -> adapter.insert(dup))
                .isInstanceOf(org.springframework.dao.DataIntegrityViolationException.class);
    }

    @Test
    @DisplayName("updatePartial() — filter 만 갱신, channels/lastNotifiedAt 보존")
    void updatePartial_filterOnly_preservesChannelsAndLastNotifiedAt() {
        NotificationSubscription created = adapter.save(NotificationSubscription.create(
                30L, "OA-UP1", Set.of(NotificationChannel.EMAIL, NotificationChannel.SMS)));
        Instant when = Instant.parse("2026-05-20T10:00:00Z");
        created.markNotified(when);
        adapter.save(created);

        NotificationSubscription after = adapter.updatePartial(
                created.getId(),
                new SubscriptionFilter(Set.of("RECEIVING"), null, null),
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
                31L, "OA-UP2", Set.of(NotificationChannel.EMAIL)));
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
                40L, "OA-DEL", Set.of(NotificationChannel.EMAIL)));

        adapter.deleteById(created.getId());

        assertThat(adapter.loadById(created.getId())).isEmpty();
    }

    @Test
    @DisplayName("saveIfAbsent() — 동일 (userId, serviceId)로 두 번 호출해도 row가 1건이다")
    void saveIfAbsent_idempotent_doesNotInsertDuplicate() {
        NotificationSubscription sub = NotificationSubscription.create(99L, "OA-2269",
                Set.of(NotificationChannel.EMAIL));
        adapter.saveIfAbsent(sub);
        adapter.saveIfAbsent(sub);  // ON CONFLICT DO NOTHING — should not throw

        List<NotificationSubscription> all = adapter.loadAll();
        long count = all.stream()
                .filter(s -> s.getUserId().equals(99L) && s.getServiceId().equals("OA-2269"))
                .count();
        assertThat(count).isEqualTo(1);
    }
}
