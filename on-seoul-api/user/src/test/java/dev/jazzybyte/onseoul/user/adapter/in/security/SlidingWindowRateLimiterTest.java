package dev.jazzybyte.onseoul.user.adapter.in.security;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.dao.QueryTimeoutException;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.script.RedisScript;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verify;
import org.mockito.ArgumentCaptor;

@ExtendWith(MockitoExtension.class)
class SlidingWindowRateLimiterTest {

    @Mock
    private StringRedisTemplate redisTemplate;

    private SlidingWindowRateLimiter newLimiter() {
        return new SlidingWindowRateLimiter(redisTemplate);
    }

    @Test
    @DisplayName("Lua 스크립트가 1을 반환하면 통과(allowed=true)")
    void allows_whenScriptReturnsOne() {
        given(redisTemplate.execute(any(RedisScript.class), anyList(), any(Object[].class)))
                .willReturn(1L);

        boolean allowed = newLimiter().tryAcquire(42L, 20, 60);

        assertThat(allowed).isTrue();
    }

    @Test
    @DisplayName("Lua 스크립트가 0을 반환하면 차단(allowed=false)")
    void blocks_whenScriptReturnsZero() {
        given(redisTemplate.execute(any(RedisScript.class), anyList(), any(Object[].class)))
                .willReturn(0L);

        boolean allowed = newLimiter().tryAcquire(42L, 20, 60);

        assertThat(allowed).isFalse();
    }

    @Test
    @DisplayName("Redis 예외 시 fail-open(allowed=true)")
    void failOpen_whenRedisThrows() {
        given(redisTemplate.execute(any(RedisScript.class), anyList(), any(Object[].class)))
                .willThrow(new QueryTimeoutException("redis down"));

        boolean allowed = newLimiter().tryAcquire(42L, 20, 60);

        assertThat(allowed).isTrue();
    }

    @Test
    @DisplayName("스크립트 결과가 null이어도 fail-open(allowed=true)")
    void failOpen_whenScriptReturnsNull() {
        given(redisTemplate.execute(any(RedisScript.class), anyList(), any(Object[].class)))
                .willReturn(null);

        boolean allowed = newLimiter().tryAcquire(42L, 20, 60);

        assertThat(allowed).isTrue();
    }

    @Test
    @DisplayName("올바른 key(rate_limit:user:{id})와 인자(now,windowMs,limit,고유멤버)로 Lua 스크립트를 실행한다")
    @SuppressWarnings("unchecked")
    void executesScriptWithCorrectKeyAndArgs() {
        given(redisTemplate.execute(any(RedisScript.class), anyList(), any(Object[].class)))
                .willReturn(1L);

        long before = System.currentTimeMillis();
        newLimiter().tryAcquire(42L, 20, 60);
        long after = System.currentTimeMillis();

        ArgumentCaptor<List<String>> keysCaptor = ArgumentCaptor.forClass(List.class);
        ArgumentCaptor<Object[]> argsCaptor = ArgumentCaptor.forClass(Object[].class);
        verify(redisTemplate).execute(any(RedisScript.class), keysCaptor.capture(), argsCaptor.capture());

        assertThat(keysCaptor.getValue()).containsExactly("rate_limit:user:42");
        Object[] args = argsCaptor.getValue();
        // ARGV[1]=now(ms), ARGV[2]=windowMs(60s), ARGV[3]=limit, ARGV[4]=고유멤버(now:seq)
        assertThat(Long.parseLong((String) args[0])).isBetween(before, after);
        assertThat(args[1]).isEqualTo("60000");
        assertThat(args[2]).isEqualTo("20");
        assertThat((String) args[3]).matches("\\d+:\\d+");
    }

    @Test
    @DisplayName("연속 호출 시 멤버가 고유하게 증가해 동일 ms ZADD 충돌을 방지한다")
    @SuppressWarnings("unchecked")
    void generatesUniqueMembersAcrossCalls() {
        given(redisTemplate.execute(any(RedisScript.class), anyList(), any(Object[].class)))
                .willReturn(1L);
        SlidingWindowRateLimiter limiter = newLimiter();

        limiter.tryAcquire(42L, 20, 60);
        limiter.tryAcquire(42L, 20, 60);

        ArgumentCaptor<Object[]> argsCaptor = ArgumentCaptor.forClass(Object[].class);
        verify(redisTemplate, org.mockito.Mockito.times(2))
                .execute(any(RedisScript.class), anyList(), argsCaptor.capture());
        List<Object[]> calls = argsCaptor.getAllValues();
        assertThat(calls.get(0)[3]).isNotEqualTo(calls.get(1)[3]);
    }
}
