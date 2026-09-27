package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
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
 * QA 회귀 테스트 — 소프트 딜리트 도입으로 <b>새로 도달 가능해진</b> 구독 부활(resurrection) 버그.
 *
 * <p>재현 경로 (메인 배치):
 * <pre>
 * NotificationScheduler.loadChunk()          → 활성 구독 sub 를 메모리에 적재
 *   ↓ (LLM 템플릿 생성 + Knock 발송 = 수 초)
 * [이 사이에 사용자가 DELETE /notifications/subscriptions/{id}] → soft delete 커밋
 *   ↓
 * NotificationTxHelper.txBSuccess()          → saveSubscriptionPort.save(sub)
 *   ↓
 * NotificationSubscriptionPersistenceAdapter.save():
 *     repository.findById(id)                → @SQLRestriction 때문에 empty
 *       .orElseGet(() -> new NotificationSubscriptionJpaEntity(...))  ← 새 엔티티
 *     repository.save(entity)                → deleted_at IS NULL 인 <b>새 행 INSERT</b>
 * </pre>
 *
 * <p>결과: 해지한 사용자에게 이후 모든 배치가 계속 알림을 발송한다. 사용자 조회 API 에도
 * 삭제한 구독이 되살아나 보인다.
 *
 * <p>변경 이전에는 도달 불가였다 — 하드 딜리트 시절 txBSuccess 에 도달한 구독은
 * 이미 dispatch 행을 갖고 있어 FK(NO ACTION) 가 삭제 자체를 막았다. 소프트 딜리트로
 * 삭제가 성공하게 되면서 레이스 윈도가 실재하게 됐다.
 *
 * <p>{@code txBRetrySuccess}(재시도 스케줄러)도 같은 {@code save()} 를 호출한다.
 * 그쪽은 앞단에 {@code loadById} empty → skip 가드가 있어 윈도가 더 좁지만 동일하게 열려 있다.
 *
 * <p>수정 위치: {@code NotificationSubscriptionPersistenceAdapter#save} — id 가 있는데
 * 조회가 empty 면 새 행을 만들지 말아야 한다(커서 전진 대상이 없으므로 no-op 또는 예외).
 * QA 는 프로덕션 코드를 수정하지 않는다.
 */
@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:notif-sub-softdelete-race;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded",
        "spring.sql.init.schema-locations=classpath:jpa-test-schema.sql"
})
@Import({NotificationSubscriptionPersistenceAdapter.class, NotificationPersistenceMapper.class})
class NotificationSubscriptionSoftDeleteRaceRegressionTest {

    private static final long USER_ID = 900L;

    @Autowired
    private NotificationSubscriptionPersistenceAdapter adapter;

    @Autowired
    private TestEntityManager em;

    @Autowired
    private JdbcTemplate jdbcTemplate;

    @Test
    @DisplayName("[BUG] 배치 진행 중 해지된 구독에 save() 가 호출되면 새 활성 구독으로 부활한다")
    void 배치_진행중_해지된_구독은_save_로_부활하지_않아야_한다() {
        NotificationSubscription saved = adapter.insert(
                NotificationSubscription.create(USER_ID, Set.of(NotificationChannel.EMAIL)));
        Long id = saved.getId();

        // 배치가 loadChunk 로 이 구독을 이미 들고 있는 상태에서 사용자가 해지
        adapter.deleteById(id);
        em.flush();
        em.clear();

        // 발송 성공 → NotificationTxHelper.txBSuccess 가 커서를 전진시키려 save() 호출
        NotificationSubscription inFlight = NotificationSubscription.ofPersistence(
                id, USER_ID, "{}", Set.of(NotificationChannel.EMAIL), null, Instant.now());
        inFlight.markNotified(Instant.now());
        adapter.save(inFlight);
        em.flush();
        em.clear();

        assertThat(activeRowCount())
                .as("해지한 구독이 활성 상태로 부활하면 이후 모든 배치가 계속 발송한다")
                .isZero();
        assertThat(adapter.loadChunk(0L, 100))
                .as("부활한 구독은 배치 발송 대상에 다시 들어온다")
                .noneMatch(s -> USER_ID == s.getUserId());
        assertThat(adapter.loadByUserId(USER_ID))
                .as("사용자 조회 API 에 삭제한 구독이 되살아나 보인다")
                .isEmpty();
    }

    private Integer activeRowCount() {
        return jdbcTemplate.queryForObject(
                "SELECT count(*) FROM notification_subscriptions WHERE user_id = ? AND deleted_at IS NULL",
                Integer.class, USER_ID);
    }
}
