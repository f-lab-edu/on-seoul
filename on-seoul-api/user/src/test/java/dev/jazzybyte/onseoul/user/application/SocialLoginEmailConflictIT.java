package dev.jazzybyte.onseoul.user.application;

import dev.jazzybyte.onseoul.crypto.AesGcmEncryptor;
import dev.jazzybyte.onseoul.crypto.BlindIndexer;
import dev.jazzybyte.onseoul.exception.EmailConflictException;
import dev.jazzybyte.onseoul.user.port.in.SocialLoginCommand;
import dev.jazzybyte.onseoul.user.port.out.RefreshTokenStorePort;
import dev.jazzybyte.onseoul.user.port.out.TokenIssuerPort;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.FilterType;
import org.springframework.context.annotation.Import;
import org.springframework.stereotype.Component;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.DockerClientFactory;
import org.testcontainers.containers.PostgreSQLContainer;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * [MUST-FIX 1 검증] OAuth 이메일 충돌이 <b>실제 PostgreSQL</b>의 {@code uq_users_email_hash}
 * 유니크 위반(23505)에서 최종적으로 {@link EmailConflictException}(EMAIL_ALREADY_REGISTERED)로
 * 귀결되는지 검증한다.
 *
 * <p><b>왜 단위 목 테스트로는 부족한가</b>: 결함은 {@code @Transactional} 경계 안에서 발생한
 * DataIntegrityViolation이 트랜잭션을 rollback-only로 마킹해, 커밋 시점에
 * {@code UnexpectedRollbackException}(DataAccessException 서브타입)이 터지고 핸들러의
 * {@code DataAccessException} catch로 빠져 {@code email_conflict}가 아닌 {@code server_error}로
 * 귀결되는 트랜잭션 매니저 상호작용이다. 목으로는 이 상호작용이 재현되지 않는다. INSERT를
 * {@code NewUserRegistrar}(REQUIRES_NEW)로 격리해 내부 트랜잭션만 롤백되게 한 뒤, 변환을
 * 경계 밖에서 수행하는 수정이 제대로 격리됐는지 실제 DB 트랜잭션으로만 확인할 수 있다.
 *
 * <p>Docker 미가용 시 {@code assumeTrue}로 전체 skip — {@code ./gradlew test}는 green 유지.
 * (NotificationDispatchCrossDedupIT의 graceful-skip 패턴을 따른다.)
 */
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@Import({
        SocialLoginEmailConflictIT.ComponentBeans.class,
        SocialLoginEmailConflictIT.TestBeans.class
})
class SocialLoginEmailConflictIT {

    private static final boolean DOCKER_AVAILABLE = DockerClientFactory.instance().isDockerAvailable();
    private static PostgreSQLContainer<?> postgres;

    @BeforeAll
    static void startContainer() {
        assumeTrue(DOCKER_AVAILABLE, "Docker 미가용 — OAuth 이메일 충돌 PG IT skip");
        postgres = new PostgreSQLContainer<>("postgres:16-alpine");
        postgres.start();
    }

    @AfterAll
    static void stopContainer() {
        if (postgres != null) {
            postgres.stop();
        }
    }

    @DynamicPropertySource
    static void datasource(DynamicPropertyRegistry registry) {
        if (!DOCKER_AVAILABLE) {
            registry.add("spring.datasource.url",
                    () -> "jdbc:h2:mem:user-conflict-skip;MODE=PostgreSQL;DB_CLOSE_DELAY=-1");
            registry.add("spring.datasource.driver-class-name", () -> "org.h2.Driver");
            registry.add("spring.sql.init.mode", () -> "never");
            registry.add("spring.jpa.hibernate.ddl-auto", () -> "none");
            return;
        }
        if (postgres == null) {
            postgres = new PostgreSQLContainer<>("postgres:16-alpine");
        }
        if (!postgres.isRunning()) {
            postgres.start();
        }
        registry.add("spring.datasource.url", postgres::getJdbcUrl);
        registry.add("spring.datasource.username", postgres::getUsername);
        registry.add("spring.datasource.password", postgres::getPassword);
        registry.add("spring.datasource.driver-class-name", () -> "org.postgresql.Driver");
        registry.add("spring.jpa.hibernate.ddl-auto", () -> "none");
        registry.add("spring.sql.init.mode", () -> "always");
        registry.add("spring.sql.init.schema-locations", () -> "classpath:pg-users-schema.sql");
    }

    @Autowired(required = false)
    private SocialLoginService socialLoginService;

    @Autowired(required = false)
    private NewUserRegistrar newUserRegistrar;

    @Autowired(required = false)
    private JdbcTemplate jdbc;

    @BeforeEach
    void cleanup() {
        assumeTrue(DOCKER_AVAILABLE);
        jdbc.update("DELETE FROM users");
    }

    @Test
    @DisplayName("선검사 경로 — 같은 이메일이 다른 벤더로 가입돼 있으면 EmailConflictException(provider=google)로 귀결된다")
    void differentProviderSameEmail_resolvesToEmailConflict() {
        // google로 최초 가입
        socialLoginService.socialLogin(
                new SocialLoginCommand("google", "google-1", "dup@example.com", "구글유저"));
        assertThat(rowCount()).isEqualTo(1);

        // kakao로 같은 이메일 로그인 → 선검사가 잡아낸다
        assertThatThrownBy(() -> socialLoginService.socialLogin(
                new SocialLoginCommand("kakao", "kakao-1", "dup@example.com", "카카오유저")))
                .isInstanceOf(EmailConflictException.class)
                .satisfies(ex -> assertThat(((EmailConflictException) ex).getExistingProvider())
                        .isEqualTo("google"));

        // 충돌이므로 신규 row가 늘지 않았다.
        assertThat(rowCount()).isEqualTo(1);
    }

    @Test
    @DisplayName("경쟁 조건 백스톱 — INSERT 단계 실제 23505 위반이 UnexpectedRollbackException이 아니라 EmailConflictException으로 변환된다")
    void raceCondition_insertUniqueViolation_resolvesToEmailConflict_notUnexpectedRollback() {
        // 첫 가입을 REQUIRES_NEW로 직접 커밋(독립 트랜잭션) — 선검사 캐시 없이 DB에만 존재.
        newUserRegistrar.register(
                new SocialLoginCommand("google", "google-2", "race@example.com", "선점유저"));
        assertThat(rowCount()).isEqualTo(1);

        // 같은 이메일/다른 벤더로 두 번째 가입을 NewUserRegistrar에 직접 호출 →
        // 실제 PG가 uq_users_email_hash 23505를 던진다. 핵심: REQUIRES_NEW 내부 트랜잭션만
        // 롤백되어야 하고, 바깥(테스트)로는 DataIntegrityViolationException 계열이 전파돼야 한다.
        // 이 예외를 SocialLoginService.registerNewUser의 catch가 변환한다.
        SocialLoginCommand racing =
                new SocialLoginCommand("kakao", "kakao-2", "race@example.com", "경합유저");

        // 검증 본질: 전체 flow가 EmailConflictException 으로 귀결되고,
        // UnexpectedRollbackException(=server_error 경로) 으로 새지 않는다.
        Throwable thrown = catchThrowable(() -> socialLoginService.socialLogin(racing));

        assertThat(thrown).isInstanceOf(EmailConflictException.class);
        // EmailConflictException 은 RuntimeException 이지 DataAccessException(=server_error) 이 아니다.
        assertThat(thrown).isNotInstanceOf(DataAccessException.class);
        assertThat(((EmailConflictException) thrown).getExistingProvider()).isNull();
        // race 경로에서도 신규 row 는 추가되지 않았다(내부 트랜잭션 롤백).
        assertThat(rowCount()).isEqualTo(1);
    }

    private long rowCount() {
        return jdbc.queryForObject("SELECT COUNT(*) FROM users", Long.class);
    }

    private static Throwable catchThrowable(Runnable r) {
        try {
            r.run();
            return null;
        } catch (Throwable t) {
            return t;
        }
    }

    /**
     * 패키지-프라이빗 {@code @Component}(UserPersistenceAdapter, UserPersistenceMapper)와
     * application 빈(SocialLoginService, NewUserRegistrar)을 컴포넌트 스캔으로 로드한다.
     * {@code @DataJpaTest}는 기본적으로 {@code @Component}를 스캔하지 않으므로 명시적으로 끌어온다.
     * 보안/웹 등 무관한 빈이 딸려오지 않도록 스캔 범위를 두 패키지로 한정한다.
     */
    @TestConfiguration
    @ComponentScan(
            basePackages = {
                    "dev.jazzybyte.onseoul.user.adapter.out.persistence",
                    "dev.jazzybyte.onseoul.user.application"
            },
            includeFilters = @ComponentScan.Filter(
                    type = FilterType.ANNOTATION, classes = {Component.class, org.springframework.stereotype.Service.class})
    )
    static class ComponentBeans {
    }

    @TestConfiguration
    static class TestBeans {

        // 32바이트(64 hex) 테스트 키 — 운영 키 아님.
        private static final String KEY_HEX =
                "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

        @Bean
        AesGcmEncryptor aesGcmEncryptor() {
            return new AesGcmEncryptor(KEY_HEX);
        }

        @Bean
        BlindIndexer blindIndexer() {
            return new BlindIndexer(KEY_HEX);
        }

        @Bean
        TokenIssuerPort tokenIssuerPort() {
            return new TokenIssuerPort() {
                @Override public String generateAccessToken(long userId) { return "at-" + userId; }
                @Override public String generateRefreshToken(long userId) { return "rt-" + userId; }
                @Override public void validateToken(String token) { }
                @Override public Long extractUserId(String token) { return 0L; }
                @Override public Optional<Long> extractUserIdSafely(String token) { return Optional.empty(); }
                @Override public long getAccessTokenMinutes() { return 30L; }
                @Override public long getRefreshTokenMinutes() { return 10080L; }
            };
        }

        @Bean
        RefreshTokenStorePort refreshTokenStorePort() {
            return new RefreshTokenStorePort() {
                @Override public void save(Long userId, String refreshToken, long ttlMinutes) { }
                @Override public Optional<String> getAndDelete(Long userId) { return Optional.empty(); }
                @Override public void delete(Long userId) { }
            };
        }
    }
}
