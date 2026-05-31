package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.crypto.AesGcmEncryptor;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import org.jooq.DSLContext;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jooq.JooqTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.util.Optional;

import static dev.jazzybyte.onseoul.jooq.Tables.USERS;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * UserContactPersistenceAdapter 통합 테스트.
 * users 테이블에 암호화된 컬럼을 직접 INSERT 후 복호화 결과를 검증한다.
 */
@JooqTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:notif-contact-test;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.sql.init.mode=embedded",
        "spring.sql.init.schema-locations=classpath:user-contact-test-schema.sql",
        "spring.jooq.sql-dialect=H2"
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
    private DSLContext dsl;

    private final AesGcmEncryptor encryptor = new AesGcmEncryptor(AES_KEY);

    @BeforeEach
    void setUp() {
        dsl.deleteFrom(USERS).execute();

        long userId = 1L;
        String emailEnc = encryptor.encrypt("user@seoul.go.kr", userId);
        String phoneEnc = encryptor.encrypt("010-9999-8888", userId);

        dsl.insertInto(USERS)
                .set(USERS.ID, userId)
                .set(USERS.PROVIDER, "google")
                .set(USERS.PROVIDER_ID, "gid-001")
                .set(USERS.EMAIL_ENC, emailEnc)
                .set(USERS.EMAIL_HASH, "somehash")
                .set(USERS.PHONE_ENC, phoneEnc)
                .set(USERS.PHONE_HASH, "phonehash")
                .set(USERS.NICKNAME, "서울시민")
                .set(USERS.STATUS, "ACTIVE")
                .execute();
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

        dsl.insertInto(USERS)
                .set(USERS.ID, userId)
                .set(USERS.PROVIDER, "kakao")
                .set(USERS.PROVIDER_ID, "kid-002")
                .set(USERS.EMAIL_ENC, emailEnc)
                .set(USERS.EMAIL_HASH, "hash2")
                .set(USERS.NICKNAME, "전화없음")
                .set(USERS.STATUS, "ACTIVE")
                .execute();

        Optional<UserContact> result = adapter.loadContact(userId);

        assertThat(result).isPresent();
        assertThat(result.get().email()).isEqualTo("noPhone@example.com");
        assertThat(result.get().phoneNumber()).isNull();
    }
}
