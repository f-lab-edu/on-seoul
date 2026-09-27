package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.annotations.SQLDelete;
import org.hibernate.annotations.SQLRestriction;
import org.hibernate.type.SqlTypes;

import java.time.Instant;

@Entity
@Table(name = "notification_subscriptions")
// 소프트 딜리트: notification_dispatches.subscription_id FK 가 NO ACTION 이라 하드 딜리트가 FK 위반이고,
// 발송 이력은 감사용으로 보존해야 한다. @SQLDelete 로 remove() 를 UPDATE 로 바꾸고
// @SQLRestriction 으로 이 엔티티가 등장하는 모든 Hibernate SQL 에 필터를 부착한다
// (findById / 파생 쿼리 / JPQL 서브쿼리 전부 커버 — 호출처별 조건 추가 불필요).
@SQLDelete(sql = "UPDATE notification_subscriptions SET deleted_at = NOW() WHERE id = ?")
@SQLRestriction("deleted_at IS NULL")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class NotificationSubscriptionJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "user_id", nullable = false)
    private Long userId;

    /**
     * JSONB in PostgreSQL; H2 test schema declares this as VARCHAR(2000).
     * Stored as a raw JSON string — VO mapping deferred to Phase 3.
     *
     * @JdbcTypeCode(SqlTypes.JSON) 은 Hibernate 6 에서 JDBC 바인딩 타입을 JSON 으로
     * 명시적으로 지정한다. columnDefinition = "jsonb" 만으로는 DDL 힌트에 그칠 뿐
     * JDBC 바인딩 타입은 여전히 character varying 으로 처리되어 PostgreSQL 이 거부한다.
     */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "filter", nullable = false, columnDefinition = "jsonb")
    private String filter;

    /**
     * 발송 채널 목록. JSONB in PostgreSQL; H2 test schema declares this as VARCHAR(500).
     * Stored as a JSON array string, e.g. ["EMAIL"] or ["EMAIL","SMS"].
     *
     * @see #filter 주석 — 동일한 이유로 @JdbcTypeCode(SqlTypes.JSON) 이 필요하다.
     */
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "channels", nullable = false, columnDefinition = "jsonb")
    private String channels;

    @Column(name = "last_notified_at")
    private Instant lastNotifiedAt;

    @CreationTimestamp
    @Column(name = "created_at", nullable = false, updatable = false)
    private Instant createdAt;

    /**
     * 소프트 딜리트 시점. NULL = 활성 구독. {@code @SQLDelete} 의 원시 SQL 이 채운다.
     *
     * <p><b>이 필드를 삭제하지 마라.</b> 코드에서 읽지 않지만 두 가지 역할이 있다.
     * <ol>
     *   <li>{@code insertable/updatable = false} — 이게 없으면 Hibernate 의 정적 UPDATE SET 목록에
     *       {@code deleted_at} 이 포함되고, 엔티티 로드 시점의 in-memory 값({@code null})이 그대로 써져
     *       동시 DELETE 를 덮어쓴다(lost update → 해지한 구독 부활). PATCH×DELETE, 배치×DELETE 두 경로에서
     *       재현된다. {@code @SQLDelete} 는 원시 SQL 이라 이 메타데이터에 영향받지 않는다.</li>
     *   <li>매핑 자체가 {@code ddl-auto: validate}(bootstrap application.yml)의 유일한 근거다 —
     *       마이그레이션 10 미적용 환경에서 부팅을 실패시킨다. {@code @SQLRestriction} 은 원시 SQL 이라
     *       validate 대상이 아니므로, 이 필드를 지우면 컬럼 없는 DB 에서 조용히 뜬 뒤 런타임에 깨진다.</li>
     * </ol>
     *
     * <p><b>결과:</b> 이 플래그 때문에 {@code deletedAt} 에 대한 엔티티 상태 기반 write 는 전부
     * <b>조용히 무시된다</b> — 컴파일 에러도 런타임 에러도 없다. 누가 {@code void restore() { this.deletedAt = null; }}
     * 나 {@code markDeleted(Instant)} 를 추가해도 아무 신호 없이 no-op 이 된다. 값을 바꾸려면
     * 원시 SQL({@code JdbcTemplate} / native query)을 써라. JPQL bulk update 는 {@code @SQLRestriction} 이
     * {@code WHERE ... AND deleted_at IS NULL} 을 덧붙여 0 rows 가 될 수 있으므로 쓰기 전에 검증이 필요하다.
     */
    @Column(name = "deleted_at", insertable = false, updatable = false)
    private Instant deletedAt;

    NotificationSubscriptionJpaEntity(Long userId, String filter, String channels) {
        this.userId = userId;
        this.filter = filter;
        this.channels = channels;
    }

    void updateLastNotifiedAt(Instant lastNotifiedAt) {
        this.lastNotifiedAt = lastNotifiedAt;
    }

    void updateFilter(String filter) {
        this.filter = filter;
    }

    void updateChannels(String channels) {
        this.channels = channels;
    }
}
