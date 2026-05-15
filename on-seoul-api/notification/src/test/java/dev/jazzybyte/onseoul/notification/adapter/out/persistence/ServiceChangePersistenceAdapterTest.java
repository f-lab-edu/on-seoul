package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.jdbc.JdbcTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.test.context.TestPropertySource;

import java.sql.Timestamp;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

@JdbcTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:notif-change-test;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.sql.init.mode=embedded",
        "spring.sql.init.schema-locations=classpath:jpa-test-schema.sql"
})
@Import(ServiceChangePersistenceAdapter.class)
class ServiceChangePersistenceAdapterTest {

    @Autowired
    private ServiceChangePersistenceAdapter adapter;

    @Autowired
    private NamedParameterJdbcTemplate jdbc;

    private static final ZoneId SEOUL = ZoneId.of("Asia/Seoul");

    @BeforeEach
    void setUp() {
        jdbc.update("DELETE FROM service_change_log", Map.of());
    }

    private void insert(String serviceId, String changeType, String fieldName,
                        String oldValue, String newValue, LocalDateTime changedAt) {
        jdbc.update(
                "INSERT INTO service_change_log (service_id, change_type, field_name, old_value, new_value, changed_at) " +
                "VALUES (:serviceId, :changeType, :fieldName, :oldValue, :newValue, :changedAt)",
                Map.of("serviceId", serviceId, "changeType", changeType,
                        "fieldName", fieldName != null ? fieldName : "",
                        "oldValue", oldValue != null ? oldValue : "",
                        "newValue", newValue != null ? newValue : "",
                        "changedAt", Timestamp.valueOf(changedAt)));
    }

    @Test
    @DisplayName("since=null이면 해당 serviceId의 전체 이력을 반환한다")
    void loadSince_nullSince_returnsAll() {
        LocalDateTime now = LocalDateTime.now();
        insert("OA-2269", "CHANGED", "status", "OPEN", "CLOSED", now.minusHours(2));
        insert("OA-2269", "CHANGED", "title", "구", "신", now.minusHours(1));
        insert("OA-2266", "NEW", null, null, null, now);

        List<ServiceChange> result = adapter.loadSince("OA-2269", null);

        assertThat(result).hasSize(2);
        assertThat(result).allMatch(c -> c.serviceId().equals("OA-2269"));
    }

    @Test
    @DisplayName("since 이후(exclusive) 데이터만 반환한다")
    void loadSince_withSince_returnsOnlyAfter() {
        LocalDateTime base = LocalDateTime.of(2026, 5, 1, 12, 0, 0);
        insert("OA-2269", "CHANGED", "status", "OPEN", "CLOSED", base);
        insert("OA-2269", "CHANGED", "title", "구", "신", base.plusHours(1));

        // since = base instant (KST)
        Instant since = base.atZone(SEOUL).toInstant();
        List<ServiceChange> result = adapter.loadSince("OA-2269", since);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).fieldName()).isEqualTo("title");
    }

    @Test
    @DisplayName("다른 serviceId의 데이터는 반환하지 않는다")
    void loadSince_differentServiceId_excluded() {
        LocalDateTime now = LocalDateTime.now();
        insert("OA-2266", "NEW", "status", null, "OPEN", now);

        List<ServiceChange> result = adapter.loadSince("OA-2269", null);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("결과는 changed_at ASC 순으로 정렬된다")
    void loadSince_orderedByChangedAtAsc() {
        LocalDateTime base = LocalDateTime.of(2026, 5, 1, 10, 0, 0);
        insert("OA-2269", "CHANGED", "f1", null, null, base.plusHours(2));
        insert("OA-2269", "CHANGED", "f2", null, null, base.plusHours(1));
        insert("OA-2269", "CHANGED", "f3", null, null, base);

        List<ServiceChange> result = adapter.loadSince("OA-2269", null);

        assertThat(result).extracting(ServiceChange::fieldName)
                .containsExactly("f3", "f2", "f1");
    }

    @Test
    @DisplayName("ServiceChange의 changedAt은 Asia/Seoul 기준으로 변환된 Instant이다")
    void loadSince_changedAtConvertedFromSeoulTimezone() {
        LocalDateTime ldt = LocalDateTime.of(2026, 5, 1, 12, 0, 0);
        insert("OA-2269", "NEW", "status", null, "OPEN", ldt);

        List<ServiceChange> result = adapter.loadSince("OA-2269", null);

        Instant expected = ldt.atZone(SEOUL).toInstant();
        assertThat(result.get(0).changedAt()).isEqualTo(expected);
    }
}
