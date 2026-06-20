package dev.jazzybyte.onseoul.user.adapter.in.security;

import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.testcontainers.DockerClientFactory;
import org.testcontainers.containers.GenericContainer;
import org.testcontainers.utility.DockerImageName;

import java.util.concurrent.atomic.AtomicInteger;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/**
 * 실제 Redis로 Lua 슬라이딩 윈도우 스크립트의 원자성/한도 동작을 검증한다.
 *
 * <p>한도(limit) 이내는 통과, 초과는 차단되며, 동시 요청에서도 정확히 limit개만 통과하는지
 * (ZREMRANGEBYSCORE→ZCARD→ZADD가 단일 스크립트로 원자 실행되는지)를 확인한다.</p>
 *
 * <p>Docker 미가용 시 {@code assumeTrue}로 전체 skip — {@code ./gradlew test}는 green 유지.
 * (프로젝트의 graceful-skip 패턴을 따른다.)</p>
 */
class SlidingWindowRateLimiterRedisIT {

    private static final boolean DOCKER_AVAILABLE = DockerClientFactory.instance().isDockerAvailable();

    private static GenericContainer<?> redis;
    private static LettuceConnectionFactory connectionFactory;
    private static StringRedisTemplate redisTemplate;
    private static SlidingWindowRateLimiter limiter;

    @BeforeAll
    static void startRedis() {
        assumeTrue(DOCKER_AVAILABLE, "Docker 미가용 — Redis 레이트 리밋 IT skip");
        redis = new GenericContainer<>(DockerImageName.parse("redis:7-alpine")).withExposedPorts(6379);
        redis.start();

        connectionFactory = new LettuceConnectionFactory(redis.getHost(), redis.getMappedPort(6379));
        connectionFactory.afterPropertiesSet();
        redisTemplate = new StringRedisTemplate(connectionFactory);
        redisTemplate.afterPropertiesSet();
        limiter = new SlidingWindowRateLimiter(redisTemplate);
    }

    @AfterAll
    static void stopRedis() {
        if (connectionFactory != null) {
            connectionFactory.destroy();
        }
        if (redis != null) {
            redis.stop();
        }
    }

    @Test
    @DisplayName("윈도우 내 limit개까지 통과, 그 이후는 차단된다")
    void allowsUpToLimitThenBlocks() {
        long userId = 1001L;
        int limit = 5;

        for (int i = 0; i < limit; i++) {
            assertThat(limiter.tryAcquire(userId, limit, 60))
                    .as("요청 %d는 한도 내이므로 통과해야 한다", i + 1)
                    .isTrue();
        }
        assertThat(limiter.tryAcquire(userId, limit, 60))
                .as("limit 초과 요청은 차단되어야 한다")
                .isFalse();
    }

    @Test
    @DisplayName("동시 요청에서도 원자성으로 정확히 limit개만 통과한다")
    void concurrentRequestsAllowExactlyLimit() throws InterruptedException {
        long userId = 2002L;
        int limit = 10;
        int threads = 50;

        AtomicInteger allowed = new AtomicInteger();
        Thread[] workers = new Thread[threads];
        for (int i = 0; i < threads; i++) {
            workers[i] = new Thread(() -> {
                if (limiter.tryAcquire(userId, limit, 60)) {
                    allowed.incrementAndGet();
                }
            });
        }
        for (Thread w : workers) w.start();
        for (Thread w : workers) w.join();

        assertThat(allowed.get())
                .as("동시 요청에서도 한도만큼만 통과해야 한다")
                .isEqualTo(limit);
    }

    @Test
    @DisplayName("서로 다른 사용자는 카운트가 격리된다")
    void separateUsersAreIsolated() {
        int limit = 3;
        IntStream.range(0, limit).forEach(i -> assertThat(limiter.tryAcquire(3003L, limit, 60)).isTrue());
        // 다른 사용자는 영향을 받지 않는다.
        assertThat(limiter.tryAcquire(4004L, limit, 60)).isTrue();
    }
}
