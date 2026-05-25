package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.crypto.AesGcmEncryptor;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.stereotype.Component;

import java.util.Optional;

/**
 * users 테이블에서 알림 발송에 필요한 연락처를 조회한 후 복호화하여 반환한다.
 *
 * <p>user BC JPA 엔티티를 직접 import하지 않고 JdbcTemplate으로 처리한다.
 * 암호화 컬럼(email_enc, phone_enc)을 조회하고 {@link AesGcmEncryptor}로 복호화한다.
 * AesGcmEncryptor는 common 모듈에 위치하므로 BC 경계를 위반하지 않는다.
 */
@Component
class UserContactPersistenceAdapter implements LoadUserContactPort {

    private static final String SQL = """
            SELECT id, email_enc, phone_enc
            FROM users
            WHERE id = :userId
            """;

    private final NamedParameterJdbcTemplate jdbc;
    private final AesGcmEncryptor encryptor;

    UserContactPersistenceAdapter(NamedParameterJdbcTemplate jdbc, AesGcmEncryptor encryptor) {
        this.jdbc = jdbc;
        this.encryptor = encryptor;
    }

    @Override
    public Optional<UserContact> loadContact(Long userId) {
        var params = new MapSqlParameterSource("userId", userId);
        return jdbc.query(SQL, params, (rs, rowNum) -> {
            long id = rs.getLong("id");
            String emailEnc = rs.getString("email_enc");
            String phoneEnc = rs.getString("phone_enc");
            return new UserContact(
                    id,
                    encryptor.decrypt(emailEnc, id),
                    encryptor.decrypt(phoneEnc, id)
            );
        }).stream().findFirst();
    }
}
