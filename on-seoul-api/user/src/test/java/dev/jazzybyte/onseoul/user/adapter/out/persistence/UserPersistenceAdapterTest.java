package dev.jazzybyte.onseoul.user.adapter.out.persistence;

import dev.jazzybyte.onseoul.crypto.AesGcmEncryptor;
import dev.jazzybyte.onseoul.crypto.BlindIndexer;
import dev.jazzybyte.onseoul.user.domain.User;
import dev.jazzybyte.onseoul.user.domain.UserStatus;
import dev.jazzybyte.onseoul.user.port.in.SocialLoginCommand;
import jakarta.persistence.EntityManager;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:testdb;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded",
        // 테스트용 256-bit hex 키
        "app.encryption.aes-key=0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20",
        "app.encryption.blind-idx-key=a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
})
@Import({
        UserPersistenceAdapter.class,
        UserPersistenceMapper.class,
        // Encryptor/Indexer를 직접 생성하여 주입
        UserPersistenceAdapterTest.TestCryptoConfig.class
})
class UserPersistenceAdapterTest {

    /** 테스트 전용 crypto bean 등록 */
    @org.springframework.boot.test.context.TestConfiguration
    static class TestCryptoConfig {
        @org.springframework.context.annotation.Bean
        AesGcmEncryptor aesGcmEncryptor() {
            return new AesGcmEncryptor("0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20");
        }

        @org.springframework.context.annotation.Bean
        BlindIndexer blindIndexer() {
            return new BlindIndexer("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2");
        }
    }

    @Autowired
    private UserPersistenceAdapter adapter;

    @Autowired
    private UserJpaRepository jpaRepository;

    @Autowired
    private EntityManager em;

    private User createAndSaveUser(String provider, String providerId, String email, String nickname) {
        User user = User.create(new SocialLoginCommand(provider, providerId, email, nickname));
        return adapter.save(user);
    }

    // ── findByProviderAndProviderId ───────────────────────────────

    @Test
    @DisplayName("findByProviderAndProviderId() — 존재하는 유저 → 반환, email 복호화됨")
    void findByProviderAndProviderId_existingUser_returnsUser() {
        createAndSaveUser("google", "google-uid-001", "user@example.com", "서울시민");

        Optional<User> result = adapter.findByProviderAndProviderId("google", "google-uid-001");

        assertThat(result).isPresent();
        assertThat(result.get().getProvider()).isEqualTo("google");
        assertThat(result.get().getProviderId()).isEqualTo("google-uid-001");
        // 복호화된 email이 원문과 동일해야 한다
        assertThat(result.get().getEmail()).isEqualTo("user@example.com");
        assertThat(result.get().getNickname()).isEqualTo("서울시민");
        assertThat(result.get().getStatus()).isEqualTo(UserStatus.ACTIVE);
    }

    @Test
    @DisplayName("findByProviderAndProviderId() — 미존재 유저 → empty")
    void findByProviderAndProviderId_nonExistent_returnsEmpty() {
        Optional<User> result = adapter.findByProviderAndProviderId("google", "non-existent-uid");

        assertThat(result).isEmpty();
    }

    // ── findById ──────────────────────────────────────────────────

    @Test
    @DisplayName("findById() — 존재하는 id → 정상 반환")
    void findById_existingUser_returnsUser() {
        User saved = createAndSaveUser("kakao", "kakao-uid-001", "kakao@example.com", "카카오유저");

        Optional<User> result = adapter.findById(saved.getId());

        assertThat(result).isPresent();
        assertThat(result.get().getId()).isEqualTo(saved.getId());
        assertThat(result.get().getProvider()).isEqualTo("kakao");
    }

    // ── save ──────────────────────────────────────────────────────

    @Test
    @DisplayName("save() 신규 유저 → insert (id 채번), DB에 평문 email 없음")
    void save_newUser_insertsAndAssignsId_noPlaintextInDb() {
        User user = User.create(new SocialLoginCommand("naver", "naver-uid-001", "naver@example.com", "네이버유저"));

        User saved = adapter.save(user);
        em.flush();
        em.clear();

        assertThat(saved.getId()).isNotNull().isPositive();
        assertThat(saved.getProvider()).isEqualTo("naver");
        assertThat(saved.getStatus()).isEqualTo(UserStatus.ACTIVE);
        // 복호화 후 이메일 검증
        assertThat(saved.getEmail()).isEqualTo("naver@example.com");

        // DB raw 컬럼 확인 — 평문이 없어야 함
        UserJpaEntity raw = jpaRepository.findById(saved.getId()).orElseThrow();
        assertThat(raw.getEmailEnc()).startsWith("v1:");
        assertThat(raw.getEmailEnc()).doesNotContain("naver@example.com");
        assertThat(raw.getEmailHash()).hasSize(64);
    }

    @Test
    @DisplayName("save() 기존 유저 → update (email 재암호화, 복호화 후 동일)")
    void save_existingUser_updatesProfile() {
        User saved = createAndSaveUser("google", "google-uid-002", "before@example.com", "변경전닉네임");

        saved.updateProfile("after@example.com", "변경후닉네임");
        User updated = adapter.save(saved);
        em.flush();
        em.clear();

        assertThat(updated.getId()).isEqualTo(saved.getId());
        assertThat(updated.getEmail()).isEqualTo("after@example.com");
        assertThat(updated.getNickname()).isEqualTo("변경후닉네임");
        assertThat(updated.getUpdatedAt()).isNotNull();

        // DB raw 컬럼 — 평문 없음
        UserJpaEntity raw = jpaRepository.findById(updated.getId()).orElseThrow();
        assertThat(raw.getEmailEnc()).startsWith("v1:");
        assertThat(raw.getEmailEnc()).doesNotContain("after@example.com");
    }

    @Test
    @DisplayName("save() updateContact() 후 → phoneNumber DB에 암호화 저장, findById()로 복원")
    void save_afterUpdateContact_persistsPhoneNumber() {
        User saved = createAndSaveUser("google", "google-uid-003", "contact@example.com", "연락처유저");
        assertThat(saved.getPhoneNumber()).isNull();

        saved.updateContact("010-5555-6666");
        adapter.save(saved);
        em.flush();
        em.clear();

        User reloaded = adapter.findById(saved.getId()).orElseThrow();
        assertThat(reloaded.getPhoneNumber()).isEqualTo("010-5555-6666");

        // DB raw 컬럼 — 평문 없음
        UserJpaEntity raw = jpaRepository.findById(saved.getId()).orElseThrow();
        assertThat(raw.getPhoneEnc()).startsWith("v1:");
        assertThat(raw.getPhoneEnc()).doesNotContain("010-5555-6666");
        assertThat(raw.getPhoneHash()).hasSize(64);
    }

    @Test
    @DisplayName("save() updateContact(null) 후 → phoneEnc, phoneHash가 null로 저장")
    void save_afterUpdateContactWithNull_persistsNullPhoneNumber() {
        User saved = createAndSaveUser("kakao", "kakao-uid-002", "clear@example.com", "지우기유저");

        saved.updateContact("010-1111-2222");
        adapter.save(saved);

        saved.updateContact(null);
        adapter.save(saved);
        em.flush();
        em.clear();

        User reloaded = adapter.findById(saved.getId()).orElseThrow();
        assertThat(reloaded.getPhoneNumber()).isNull();

        UserJpaEntity raw = jpaRepository.findById(saved.getId()).orElseThrow();
        assertThat(raw.getPhoneEnc()).isNull();
        assertThat(raw.getPhoneHash()).isNull();
    }

    // ── findByEmailHash ───────────────────────────────────────────

    @Test
    @DisplayName("findByEmailHash() — 정규화된 이메일 hash로 조회 성공")
    void findByEmailHash_existingEmail_returnsUser() {
        createAndSaveUser("google", "google-uid-005", "Seoul@Example.COM", "대문자유저");

        BlindIndexer indexer = new BlindIndexer("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2");
        // 소문자 정규화된 이메일로 hash 계산
        String hash = indexer.index("seoul@example.com", "email");

        Optional<User> result = adapter.findByEmailHash(hash);

        assertThat(result).isPresent();
        assertThat(result.get().getEmail()).isEqualTo("Seoul@Example.COM");
    }

    @Test
    @DisplayName("findByEmailHash() — 없으면 empty 반환")
    void findByEmailHash_missing_returnsEmpty() {
        BlindIndexer indexer = new BlindIndexer("a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2");
        String hash = indexer.index("ghost@nowhere.com", "email");

        assertThat(adapter.findByEmailHash(hash)).isEmpty();
    }

    // ── 2-phase save: AAD 재암호화 검증 ──────────────────────────────

    @Test
    @DisplayName("save() 신규 유저 — 최종 email_enc는 실제 userId(AAD)로 복호화 가능, AAD=0L로는 복호화 실패")
    void save_newUser_finalEmailEncUsesRealUserIdAAD() {
        AesGcmEncryptor encryptor = new AesGcmEncryptor(
                "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20");

        User user = User.create(new SocialLoginCommand("naver", "naver-uid-aad", "aad-test@example.com", "AAD테스트"));
        User saved = adapter.save(user);
        em.flush();
        em.clear();

        UserJpaEntity raw = jpaRepository.findById(saved.getId()).orElseThrow();
        long realUserId = raw.getId();

        // 실제 userId AAD로 복호화 성공
        String decrypted = encryptor.decrypt(raw.getEmailEnc(), realUserId);
        assertThat(decrypted).isEqualTo("aad-test@example.com");

        // AAD=0L (placeholder)로 복호화 시 AEADBadTagException 발생 — Copy Attack 방어
        assertThatThrownBy(() -> encryptor.decrypt(raw.getEmailEnc(), 0L))
                .hasCauseInstanceOf(javax.crypto.AEADBadTagException.class);
    }

    @Test
    @DisplayName("save() phone null 사용자 — phoneEnc/phoneHash null 저장, 이후 findById() 복원")
    void save_newUser_nullPhone_persistsAndLoadsCorrectly() {
        User user = User.create(new SocialLoginCommand("google", "google-uid-nullphone", "nullphone@example.com", "전화없음"));

        User saved = adapter.save(user);
        em.flush();
        em.clear();

        // phone이 null이어도 저장 성공
        assertThat(saved.getPhoneNumber()).isNull();

        UserJpaEntity raw = jpaRepository.findById(saved.getId()).orElseThrow();
        assertThat(raw.getPhoneEnc()).isNull();
        assertThat(raw.getPhoneHash()).isNull();

        // findById 복원 — phoneNumber null 유지
        User reloaded = adapter.findById(saved.getId()).orElseThrow();
        assertThat(reloaded.getPhoneNumber()).isNull();
        assertThat(reloaded.getEmail()).isEqualTo("nullphone@example.com");
    }
}
