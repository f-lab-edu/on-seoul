package dev.jazzybyte.onseoul.user.adapter.in.security;

import lombok.extern.slf4j.Slf4j;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.DefaultRedisScript;
import org.springframework.data.redis.core.script.RedisScript;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Redis ZSET + Lua 원자 스크립트 기반 슬라이딩 윈도우 카운터.
 *
 * <p>key {@code rate_limit:user:{userId}} 의 ZSET에 요청 타임스탬프(ms)를 score로 적재한다.
 * 윈도우 밖 항목을 제거한 뒤 현재 카운트가 한도 미만이면 통과시키고 멤버를 추가한다.
 * 제거→카운트→추가→만료설정을 단일 Lua 스크립트로 원자 실행해 동시 요청 간 race를 막는다.</p>
 *
 * <p>동일 ms 충돌 시 ZADD가 score만 갱신해 카운트가 누락되지 않도록, 멤버를
 * {@code now:{증가시퀀스}} 로 고유하게 만든다. score는 now(ms)를 그대로 사용한다.</p>
 *
 * <p>Redis/Lua 예외 시 fail-open(통과)한다. 가용성을 정확성보다 우선한다.</p>
 */
@Slf4j
@Component
public class SlidingWindowRateLimiter {

    private static final String KEY_PREFIX = "rate_limit:user:";

    /**
     * KEYS[1] = ZSET key
     * ARGV[1] = now(ms), ARGV[2] = windowMs, ARGV[3] = limit, ARGV[4] = 고유 멤버
     * 반환: 통과 1, 차단 0
     */
    private static final RedisScript<Long> SLIDING_WINDOW_SCRIPT = new DefaultRedisScript<>(
            """
            local key = KEYS[1]
            local now = tonumber(ARGV[1])
            local windowMs = tonumber(ARGV[2])
            local limit = tonumber(ARGV[3])
            local member = ARGV[4]
            redis.call('ZREMRANGEBYSCORE', key, 0, now - windowMs)
            local count = redis.call('ZCARD', key)
            if count < limit then
              redis.call('ZADD', key, now, member)
              redis.call('PEXPIRE', key, windowMs)
              return 1
            end
            redis.call('PEXPIRE', key, windowMs)
            return 0
            """,
            Long.class);

    private final StringRedisTemplate redisTemplate;
    private final AtomicLong sequence = new AtomicLong();

    public SlidingWindowRateLimiter(final StringRedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    /**
     * 사용자 단위로 윈도우 내 요청 1건을 시도한다.
     *
     * @return 통과 시 true, 한도 초과 시 false. Redis 장애 시 fail-open으로 true.
     */
    public boolean tryAcquire(long userId, int requestsPerMinute, int windowSeconds) {
        long now = System.currentTimeMillis();
        long windowMs = windowSeconds * 1000L;
        String member = now + ":" + sequence.incrementAndGet();
        try {
            Long result = redisTemplate.execute(
                    SLIDING_WINDOW_SCRIPT,
                    List.of(KEY_PREFIX + userId),
                    Long.toString(now),
                    Long.toString(windowMs),
                    Integer.toString(requestsPerMinute),
                    member);
            // null 등 비정상 응답도 fail-open 처리한다.
            return result == null || result == 1L;
        } catch (Exception e) {
            log.warn("[RateLimit] Redis 오류 - fail-open 통과 - userId={}", userId, e);
            return true;
        }
    }
}
