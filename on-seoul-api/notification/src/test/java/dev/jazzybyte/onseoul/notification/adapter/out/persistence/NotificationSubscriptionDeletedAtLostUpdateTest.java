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
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 회귀 테스트 — {@code deleted_at} lost update 로 인한 구독 부활.
 *
 * <p>{@code @DynamicUpdate} 가 없으므로 Hibernate 는 부팅 시 정적 UPDATE 하나를 만들고 SET 목록에
 * updatable 컬럼 전부를 넣는다. {@code deleted_at} 이 updatable 이면 SET 에 포함되고, 값은 엔티티
 * 로드 시점의 in-memory 상태({@code null})라 그 사이 커밋된 DELETE 를 덮어쓴다.
 *
 * <pre>
 * update notification_subscriptions set channels=?, deleted_at=?, filter=?, last_notified_at=? where id=?
 *                                                   ^^^^^^^^^^^^ null 로 되돌아가 행이 재활성화된다
 * </pre>
 *
 * <p>재현 경로 2종:
 * <ul>
 *   <li>PATCH × DELETE — {@code NotificationSubscriptionService.update()} 는 단일 트랜잭션
 *       (READ COMMITTED)이라 엔티티 로드와 커밋 사이 창이 넓다. 사용자가 다른 기기/탭에서 유발 가능.</li>
 *   <li>배치 × DELETE — {@code save()} 의 {@code findById} 가드를 통과한 직후 DELETE 가 커밋되면
 *       {@code txBSuccess} 커밋 시 같은 UPDATE 가 나간다.</li>
 * </ul>
 *
 * <p>스레드 없이 결정적으로 재현한다: 엔티티를 영속 상태로 로드해 dirty 로 만든 뒤(=영속성 컨텍스트가
 * {@code deletedAt = null} 을 들고 있는 상태) JdbcTemplate 으로 {@code deleted_at} 을 직접 세팅하고
 * flush 한다. {@code @Column(insertable = false, updatable = false)} 를 제거하면 실패한다.
 */
@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:notif-sub-lostupdate;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded",
        "spring.sql.init.schema-locations=classpath:jpa-test-schema.sql"
})
@Import({NotificationSubscriptionPersistenceAdapter.class, NotificationPersistenceMapper.class})
class NotificationSubscriptionDeletedAtLostUpdateTest {

    @Autowired
    private NotificationSubscriptionPersistenceAdapter adapter;

    @Autowired
    private TestEntityManager em;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Test
    @DisplayName("PATCH × DELETE — updatePartial 의 UPDATE 가 동시 커밋된 deleted_at 을 덮어쓰지 않는다")
    void updatePartial_동시_해지를_덮어쓰지_않는다() {
        long userId = 910L;
        Long id = adapter.insert(NotificationSubscription.create(
                userId, Set.of(NotificationChannel.EMAIL))).getId();
        em.flush();
        em.clear();

        // PATCH 가 엔티티를 로드해 변경 (아직 flush 전 — deletedAt = null 을 캐시한 상태)
        adapter.updatePartial(id, new SubscriptionFilter(Set.of("RECEIVING"), null, null, null), null);

        // 그 사이 다른 기기/탭에서 DELETE 커밋
        softDeleteOutOfBand(id);

        em.flush();   // PATCH 커밋 → SET 목록에 deleted_at 이 있으면 여기서 부활한다

        assertThat(activeRowCount(userId))
                .as("PATCH 의 UPDATE 가 deleted_at 을 NULL 로 되돌리면 해지가 무효화된다")
                .isZero();
    }

    @Test
    @DisplayName("배치 × DELETE — save() 의 last_notified_at 전진이 동시 커밋된 deleted_at 을 덮어쓰지 않는다")
    void save_동시_해지를_덮어쓰지_않는다() {
        long userId = 911L;
        NotificationSubscription saved = adapter.insert(NotificationSubscription.create(
                userId, Set.of(NotificationChannel.EMAIL)));
        em.flush();
        em.clear();

        // txBSuccess: findById 가드를 통과(아직 활성)한 뒤 커서 전진
        saved.markNotified(Instant.parse("2026-06-01T00:00:00Z"));
        adapter.save(saved);

        // 가드 통과 직후 DELETE 커밋
        softDeleteOutOfBand(saved.getId());

        em.flush();

        assertThat(activeRowCount(userId))
                .as("커서 전진 UPDATE 가 deleted_at 을 NULL 로 되돌리면 해지한 사용자에게 계속 발송된다")
                .isZero();
    }

    /** 다른 트랜잭션이 커밋한 소프트 딜리트 — 영속성 컨텍스트를 우회해 DB 행만 바꾼다. */
    private void softDeleteOutOfBand(Long id) {
        jdbcTemplate.update(
                "UPDATE notification_subscriptions SET deleted_at = NOW() WHERE id = ?", id);
    }

    private Integer activeRowCount(long userId) {
        return jdbcTemplate.queryForObject(
                "SELECT count(*) FROM notification_subscriptions WHERE user_id = ? AND deleted_at IS NULL",
                Integer.class, userId);
    }
}
