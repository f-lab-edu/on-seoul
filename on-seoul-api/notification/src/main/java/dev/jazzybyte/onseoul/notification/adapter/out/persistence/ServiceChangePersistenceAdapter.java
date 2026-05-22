package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Component;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.util.List;

/**
 * service_change_log JOIN public_service_reservations 결과를 SubscriptionFilter 조건으로 필터링한다.
 *
 * <p>도메인은 SQL을 알지 못한다 — 이 어댑터가 SubscriptionFilter의 필드를 읽어 WHERE 절을 동적 구성한다.
 * 도메인은 카테고리/지역/상태 같은 구조화된 필드만 노출.
 */
@Component
class ServiceChangePersistenceAdapter implements LoadServiceChangePort {

    /**
     * service_change_log L JOIN public_service_reservations P ON L.service_id = P.service_id.
     *
     * 동적 WHERE:
     *   - L.service_id = :serviceId
     *   - :since IS NULL OR L.changed_at > :since
     *   - filter.statuses     비었으면 무시, 아니면 P.service_status   IN (:statuses)
     *   - filter.areaNames    비었으면 무시, 아니면 P.area_name        IN (:areaNames)
     *   - filter.maxClassNames 비었으면 무시, 아니면 P.max_class_name IN (:maxClassNames)
     *   - P.deleted_at IS NULL  (소프트 삭제 제외)
     */
    private static final String BASE_SQL = """
            SELECT L.id, L.service_id, L.change_type, L.field_name, L.old_value, L.new_value, L.changed_at
            FROM service_change_log L
            JOIN public_service_reservations P ON P.service_id = L.service_id
            WHERE L.service_id = :serviceId
              AND P.deleted_at IS NULL
              AND (:since IS NULL OR L.changed_at > :since)
            """;

    private final NamedParameterJdbcTemplate jdbc;

    ServiceChangePersistenceAdapter(final NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public List<ServiceChange> loadFiltered(String serviceId, SubscriptionFilter filter,
                                            Instant lastNotifiedAt) {
        SubscriptionFilter f = filter == null ? SubscriptionFilter.empty() : filter;
        Timestamp sinceTs = lastNotifiedAt == null ? null : Timestamp.from(lastNotifiedAt);

        MapSqlParameterSource params = new MapSqlParameterSource()
                .addValue("serviceId", serviceId)
                .addValue("since", sinceTs);

        StringBuilder sql = new StringBuilder(BASE_SQL);

        if (!f.statuses().isEmpty()) {
            sql.append(" AND P.service_status IN (:statuses)");
            params.addValue("statuses", f.statuses());
        }
        if (!f.areaNames().isEmpty()) {
            sql.append(" AND P.area_name IN (:areaNames)");
            params.addValue("areaNames", f.areaNames());
        }
        if (!f.maxClassNames().isEmpty()) {
            sql.append(" AND P.max_class_name IN (:maxClassNames)");
            params.addValue("maxClassNames", f.maxClassNames());
        }

        sql.append(" ORDER BY L.changed_at ASC");

        return jdbc.query(sql.toString(), params, new ServiceChangeRowMapper());
    }

    private static final class ServiceChangeRowMapper implements RowMapper<ServiceChange> {
        @Override
        public ServiceChange mapRow(ResultSet rs, int rowNum) throws SQLException {
            OffsetDateTime odt = rs.getObject("changed_at", OffsetDateTime.class);
            Instant changedAt = (odt != null) ? odt.toInstant() : Instant.now();

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
