package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
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
