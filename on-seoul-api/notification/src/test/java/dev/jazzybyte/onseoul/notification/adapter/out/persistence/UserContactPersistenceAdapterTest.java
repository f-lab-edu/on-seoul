package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.crypto.AesGcmEncryptor;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.namedparam.MapSqlParameterSource;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.test.context.TestPropertySource;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * UserContactPersistenceAdapter 통합 테스트.
 * users 테이블에 암호화된 컬럼을 직접 INSERT 후 복호화 결과를 검증한다.
 */
@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:notif-contact-test;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded",
        "spring.sql.init.schema-locations=classpath:user-contact-test-schema.sql"
})
@Import({
        UserContactPersistenceAdapter.class,
        UserContactPersistenceAdapterTest.TestCryptoConfig.class
})
class UserContactPersistenceAdapterTest {

    static final String AES_KEY = "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20";

    @TestConfiguration
    static class TestCryptoConfig {
        @Bean
        AesGcmEncryptor aesGcmEncryptor() {
            return new AesGcmEncryptor(AES_KEY);
        }
    }

    @Autowired
    private UserContactPersistenceAdapter adapter;

    @Autowired
    private NamedParameterJdbcTemplate jdbc;

    private final AesGcmEncryptor encryptor = new AesGcmEncryptor(AES_KEY);

    @BeforeEach
    void insertUser() {
        // userId=1 로 암호화된 email/phone 삽입
        long userId = 1L;
        String emailEnc = encryptor.encrypt("user@seoul.go.kr", userId);
        String phoneEnc = encryptor.encrypt("010-9999-8888", userId);

        String sql = """
                INSERT INTO users (id, provider, provider_id, email_enc, email_hash, phone_enc, phone_hash, nickname, status, created_at, updated_at)
                VALUES (:id, 'google', 'gid-001', :emailEnc, 'somehash', :phoneEnc, 'phonehash', '서울시민', 'ACTIVE', NOW(), NOW())
                """;
        jdbc.update(sql, new MapSqlParameterSource()
                .addValue("id", userId)
                .addValue("emailEnc", emailEnc)
                .addValue("phoneEnc", phoneEnc));
    }

    @Test
    @DisplayName("loadContact() — 암호화된 컬럼에서 복호화 후 UserContact 반환")
    void loadContact_decryptsAndReturnsUserContact() {
        Optional<UserContact> result = adapter.loadContact(1L);

        assertThat(result).isPresent();
        assertThat(result.get().userId()).isEqualTo(1L);
        assertThat(result.get().email()).isEqualTo("user@seoul.go.kr");
        assertThat(result.get().phoneNumber()).isEqualTo("010-9999-8888");
    }

    @Test
    @DisplayName("loadContact() — 없는 userId → empty 반환")
    void loadContact_missingUser_returnsEmpty() {
        assertThat(adapter.loadContact(9999L)).isEmpty();
    }

    @Test
    @DisplayName("loadContact() — phone_enc가 null인 유저 → phoneNumber null 반환")
    void loadContact_nullPhone_returnsNullPhoneNumber() {
        long userId = 2L;
        String emailEnc = encryptor.encrypt("noPhone@example.com", userId);
        jdbc.update("""
                INSERT INTO users (id, provider, provider_id, email_enc, email_hash, nickname, status, created_at, updated_at)
                VALUES (:id, 'kakao', 'kid-002', :emailEnc, 'hash2', '전화없음', 'ACTIVE', NOW(), NOW())
                """,
                new MapSqlParameterSource()
                        .addValue("id", userId)
                        .addValue("emailEnc", emailEnc));

        Optional<UserContact> result = adapter.loadContact(userId);

        assertThat(result).isPresent();
        assertThat(result.get().email()).isEqualTo("noPhone@example.com");
        assertThat(result.get().phoneNumber()).isNull();
    }
}
