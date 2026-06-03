package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.jdbc.AutoConfigureTestDatabase;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.testcontainers.DockerClientFactory;
import org.testcontainers.containers.PostgreSQLContainer;

import java.time.LocalDate;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * [M2] CHANGE↔시점 cross-trigger dedup 의 JSONB containment 경로를 <b>실제 PostgreSQL</b>로 검증한다.
 *
 * <p>배경: {@code payload->'services' @> CAST(? AS jsonb)} 는 PG 전용 연산자라 H2 단위테스트
 * (NotificationDispatchPersistenceAdapterTest)에서는 null-인자 방어 경로와 리터럴 형태만 검증할 수
 * 있었고, containment 매칭 자체는 자동 검증이 전무했다. 본 IT 가 그 공백을 메운다.
 *
 * <p><b>Docker 부재 graceful skip</b>: {@code @Testcontainers}/{@code @Container} 의 확장 관리 대신
 * 컨테이너를 <b>수동</b>으로 시작·종료한다(@Container 가 null 필드를 만나 ExtensionConfigurationException
 * 을 던지는 문제 회피). {@link DockerClientFactory#isDockerAvailable()} 로 가용성을 확인하고, 미가용 시
 * {@code assumeTrue} 로 모든 테스트를 skip 한다. Docker 없이도 {@code ./gradlew test} 는 green 이다.
 *
 * <p>스키마: {@code pg-crossdedup-schema.sql} (migration 09/11/12 의 notification_dispatches 컬럼·
 * 부분 인덱스를 PG 네이티브 JSONB 형태로 재현). 행은 JdbcTemplate 으로 직접 INSERT 해 payload 형태를
 * 정밀 제어한다.
 */
@DataJpaTest
@AutoConfigureTestDatabase(replace = AutoConfigureTestDatabase.Replace.NONE)
@Import({
        NotificationDispatchPersistenceAdapter.class,
        NotificationPersistenceMapper.class
})
class NotificationDispatchCrossDedupIT {

    private static final boolean DOCKER_AVAILABLE = DockerClientFactory.instance().isDockerAvailable();

    // @Container 미사용 — Docker 미가용 시 컨테이너를 만들지 않아 확장 오류를 피한다(아래 @BeforeAll/@AfterAll 수동 관리).
    private static PostgreSQLContainer<?> postgres;

    @BeforeAll
    static void startContainer() {
        assumeTrue(DOCKER_AVAILABLE, "Docker 미가용 — cross-dedup PG IT skip");
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
        // DynamicPropertySource 는 Spring 컨텍스트 로딩 전에 실행된다.
        // Docker 미가용 시: 컨텍스트가 그래도 로드되도록 H2 로 폴백한다(모든 테스트는 assumeTrue 로 skip).
        //   replace=NONE 이라 configured datasource 가 필요하므로, 빈 채로 두면 컨텍스트 로딩이 깨진다.
        if (!DOCKER_AVAILABLE) {
            registry.add("spring.datasource.url",
                    () -> "jdbc:h2:mem:crossdedup-skip;MODE=PostgreSQL;DB_CLOSE_DELAY=-1");
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
        registry.add("spring.sql.init.schema-locations", () -> "classpath:pg-crossdedup-schema.sql");
    }

    @Autowired(required = false)
    private NotificationDispatchPersistenceAdapter adapter;

    @Autowired(required = false)
    private NotificationDispatchJpaRepository repository;

    @Autowired(required = false)
    private JdbcTemplate jdbc;

    private static final LocalDate TODAY = LocalDate.of(2026, 6, 3);

    @BeforeEach
    void cleanup() {
        assumeTrue(DOCKER_AVAILABLE);
        jdbc.update("DELETE FROM notification_dispatches");
    }

    /**
     * CHANGE dispatch 1건을 JdbcTemplate 으로 직접 INSERT 한다.
     *
     * @param servicesJson notification_payload 의 services 배열 JSON(예: {@code [{"serviceId":"OA-1","name":"x"}]}).
     */
    private void insertChangeDispatch(long subscriptionId, LocalDate dispatchDate,
                                      String triggerType, String servicesJson) {
        String payload = "{\"title\":\"t\",\"summary\":\"s\",\"services\":" + servicesJson + "}";
        jdbc.update("""
                INSERT INTO notification_dispatches
                    (batch_id, subscription_id, trigger_type, dispatch_date, status, notification_payload)
                VALUES (?, ?, ?, ?, 'PENDING', CAST(? AS jsonb))
                """,
                System.nanoTime() & 0x7fffffff, subscriptionId, triggerType, dispatchDate, payload);
    }

    @Test
    @DisplayName("(a) CHANGE payload services:[{serviceId:X,...}] 가 배열 리터럴 [{serviceId:X}] 로 @> 매칭 → true")
    void changePayloadCoversService_matchesArrayLiteral() {
        insertChangeDispatch(1L, TODAY, "CHANGE",
                "[{\"serviceId\":\"OA-2269\",\"name\":\"수영교실\",\"status\":\"접수중\"}]");

        // adapter.existsChangeDispatchForServiceToday → servicesContainmentLiteral 로 [{"serviceId":"X"}] 생성
        assertThat(adapter.existsChangeDispatchForServiceToday(1L, "OA-2269", TODAY)).isTrue();
    }

    @Test
    @DisplayName("(b) 회귀가드 — 잘못된 단일 객체 리터럴 {serviceId:X} 로는 배열⊇객체 비교가 성립 안 해 false")
    void singleObjectLiteral_doesNotMatch() {
        insertChangeDispatch(1L, TODAY, "CHANGE",
                "[{\"serviceId\":\"OA-2269\",\"name\":\"수영교실\"}]");

        // 올바른 배열 리터럴은 매칭(adapter 가 servicesContainmentLiteral 로 배열을 만든다).
        assertThat(adapter.existsChangeDispatchForServiceToday(1L, "OA-2269", TODAY)).isTrue();

        // 잘못된 단일 객체 리터럴 '{"serviceId":"X"}' 은 배열 우변과 ⊇ 비교가 안 돼 false 여야 한다.
        boolean wrongShape = repository.existsChangeDispatchCoveringService(
                1L, TODAY, "{\"serviceId\":\"OA-2269\"}");
        assertThat(wrongShape).isFalse();
    }

    @Test
    @DisplayName("(c) 다른 serviceId / 다른 subscription / 다른 dispatch_date / trigger_type!=CHANGE 는 매칭 안 됨")
    void nonMatchingDimensions_returnFalse() {
        insertChangeDispatch(1L, TODAY, "CHANGE",
                "[{\"serviceId\":\"OA-2269\"}]");

        // 다른 serviceId
        assertThat(adapter.existsChangeDispatchForServiceToday(1L, "OA-OTHER", TODAY)).isFalse();
        // 다른 subscription
        assertThat(adapter.existsChangeDispatchForServiceToday(2L, "OA-2269", TODAY)).isFalse();
        // 다른 dispatch_date
        assertThat(adapter.existsChangeDispatchForServiceToday(1L, "OA-2269", TODAY.plusDays(1))).isFalse();

        // trigger_type != CHANGE 인 행만 있으면 매칭 안 됨(부분 인덱스/술어가 CHANGE 행만 본다).
        jdbc.update("DELETE FROM notification_dispatches");
        insertChangeDispatch(1L, TODAY, "DEADLINE_DDAY",
                "[{\"serviceId\":\"OA-2269\"}]");
        assertThat(adapter.existsChangeDispatchForServiceToday(1L, "OA-2269", TODAY)).isFalse();
    }

    @Test
    @DisplayName("payload.services 에 여러 serviceId 가 묶여도 그중 하나로 매칭된다(CHANGE 는 N service 묶음)")
    void multipleServicesInPayload_matchesAny() {
        insertChangeDispatch(1L, TODAY, "CHANGE",
                "[{\"serviceId\":\"OA-A\"},{\"serviceId\":\"OA-B\"},{\"serviceId\":\"OA-C\"}]");

        assertThat(adapter.existsChangeDispatchForServiceToday(1L, "OA-A", TODAY)).isTrue();
        assertThat(adapter.existsChangeDispatchForServiceToday(1L, "OA-B", TODAY)).isTrue();
        assertThat(adapter.existsChangeDispatchForServiceToday(1L, "OA-C", TODAY)).isTrue();
        assertThat(adapter.existsChangeDispatchForServiceToday(1L, "OA-D", TODAY)).isFalse();
    }

    @Test
    @DisplayName("특수문자 serviceId(따옴표/대괄호/중괄호)도 servicesContainmentLiteral 이스케이프로 정확히 매칭")
    void specialCharServiceId_escapedAndMatches() {
        String tricky = "a\"b]{}c";
        insertChangeDispatch(1L, TODAY, "CHANGE",
                "[{\"serviceId\":" + jsonString(tricky) + "}]");

        assertThat(adapter.existsChangeDispatchForServiceToday(1L, tricky, TODAY)).isTrue();
        // 다른 값은 매칭 안 됨(이스케이프가 과잉 매칭으로 새지 않음).
        assertThat(adapter.existsChangeDispatchForServiceToday(1L, "a\"b", TODAY)).isFalse();
    }

    /** 작은 JSON 문자열 리터럴 헬퍼(테스트 입력 구성용 — 최소 이스케이프). */
    private static String jsonString(String s) {
        return "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }
}
