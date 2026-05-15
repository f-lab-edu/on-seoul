package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Component;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.ZoneId;
import java.util.List;

/**
 * service_change_log 테이블을 JdbcTemplate으로 직접 조회한다.
 * collection BC의 JPA 엔티티를 import하지 않는다.
 */
@Component
class ServiceChangePersistenceAdapter implements LoadServiceChangePort {

    private static final ZoneId SEOUL = ZoneId.of("Asia/Seoul");

    private static final String LOAD_SINCE_SQL = """
            SELECT id, service_id, change_type, field_name, old_value, new_value, changed_at
            FROM service_change_log
            WHERE service_id = :serviceId
              AND (:since IS NULL OR changed_at > :since)
            ORDER BY changed_at ASC
            """;

    private final NamedParameterJdbcTemplate jdbc;

    ServiceChangePersistenceAdapter(final NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public List<ServiceChange> loadSince(String serviceId, Instant since) {
        Timestamp sinceTs = since == null ? null
                : Timestamp.from(since);

        MapSqlParameterSource params = new MapSqlParameterSource()
                .addValue("serviceId", serviceId)
                .addValue("since", sinceTs);

        return jdbc.query(LOAD_SINCE_SQL, params, new ServiceChangeRowMapper());
    }

    private static final class ServiceChangeRowMapper implements RowMapper<ServiceChange> {
        @Override
        public ServiceChange mapRow(ResultSet rs, int rowNum) throws SQLException {
            Timestamp changedAtTs = rs.getTimestamp("changed_at");
            Instant changedAt = changedAtTs == null ? null
                    : changedAtTs.toLocalDateTime().atZone(SEOUL).toInstant();

            return new ServiceChange(
                    rs.getLong("id"),
                    rs.getString("service_id"),
                    rs.getString("change_type"),
                    rs.getString("field_name"),
                    rs.getString("old_value"),
                    rs.getString("new_value"),
                    changedAt
            );
        }
    }
}
