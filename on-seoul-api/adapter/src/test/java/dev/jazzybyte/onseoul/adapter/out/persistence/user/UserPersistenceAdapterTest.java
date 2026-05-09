package dev.jazzybyte.onseoul.adapter.out.persistence.user;

import dev.jazzybyte.onseoul.domain.model.User;
import dev.jazzybyte.onseoul.domain.model.UserStatus;
import dev.jazzybyte.onseoul.domain.port.in.SocialLoginCommand;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:testdb;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded"
})
@Import({UserPersistenceAdapter.class, UserPersistenceMapper.class})
class UserPersistenceAdapterTest {

    @Autowired
    private UserPersistenceAdapter adapter;

    private User createAndSaveUser(String provider, String providerId, String email, String nickname) {
        User user = User.create(new SocialLoginCommand(provider, providerId, email, nickname));
        return adapter.save(user);
    }

    // ── findByProviderAndProviderId ───────────────────────────────

    @Test
    @DisplayName("findByProviderAndProviderId() — 존재하는 유저 → 반환")
    void findByProviderAndProviderId_existingUser_returnsUser() {
        createAndSaveUser("google", "google-uid-001", "user@example.com", "서울시민");

        Optional<User> result = adapter.findByProviderAndProviderId("google", "google-uid-001");

        assertThat(result).isPresent();
        assertThat(result.get().getProvider()).isEqualTo("google");
        assertThat(result.get().getProviderId()).isEqualTo("google-uid-001");
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
    @DisplayName("save() 신규 유저 → insert (id 채번)")
    void save_newUser_insertsAndAssignsId() {
        User user = User.create(new SocialLoginCommand("naver", "naver-uid-001", "naver@example.com", "네이버유저"));

        User saved = adapter.save(user);

        assertThat(saved.getId()).isNotNull().isPositive();
        assertThat(saved.getProvider()).isEqualTo("naver");
        assertThat(saved.getProviderId()).isEqualTo("naver-uid-001");
        assertThat(saved.getStatus()).isEqualTo(UserStatus.ACTIVE);
    }

    @Test
    @DisplayName("save() 기존 유저 → update (updatedAt 갱신)")
    void save_existingUser_updatesProfile() {
        User saved = createAndSaveUser("google", "google-uid-002", "before@example.com", "변경전닉네임");

        // profile update
        saved.updateProfile("after@example.com", "변경후닉네임");
        User updated = adapter.save(saved);

        assertThat(updated.getId()).isEqualTo(saved.getId());
        assertThat(updated.getEmail()).isEqualTo("after@example.com");
        assertThat(updated.getNickname()).isEqualTo("변경후닉네임");
        assertThat(updated.getUpdatedAt()).isNotNull();
    }
}
