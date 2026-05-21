package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Component;

import java.util.Optional;

/**
 * users 테이블에서 알림 발송에 필요한 연락처만 조회한다.
 * user BC JPA 엔티티를 직접 import하지 않고 JdbcTemplate으로 처리한다.
 */
@Component
class UserContactPersistenceAdapter implements LoadUserContactPort {

    private static final String SQL = """
            SELECT id, email, phone_number
            FROM users
            WHERE id = :userId
            """;

    private final NamedParameterJdbcTemplate jdbc;

    UserContactPersistenceAdapter(NamedParameterJdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    @Override
    public Optional<UserContact> loadContact(Long userId) {
        var params = new MapSqlParameterSource("userId", userId);
        return jdbc.query(SQL, params, (rs, rowNum) -> new UserContact(
                rs.getLong("id"),
                rs.getString("email"),
                rs.getString("phone_number")
        )).stream().findFirst();
    }
}
