package dev.jazzybyte.onseoul.adapter.out.redis;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;

import java.util.Optional;
import java.util.concurrent.TimeUnit;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class RefreshTokenRedisAdapterTest {

    @Mock
    private StringRedisTemplate redisTemplate;

    @Mock
    private ValueOperations<String, String> valueOperations;

    private RefreshTokenRedisAdapter adapter;

    private static final Long USER_ID = 42L;
    private static final String KEY = "RT:" + USER_ID;
    private static final String TOKEN_VALUE = "refresh-token-value";

    @BeforeEach
    void setUp() {
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        adapter = new RefreshTokenRedisAdapter(redisTemplate);
    }

    // -----------------------------------------------------------------------
    // T8-1: save() → TTL 포함해 저장됨
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("save()는 'RT:{userId}' 키로 토큰 값을 지정한 TTL(분)과 함께 저장한다")
    void save_storesTokenWithTtl() {
        long ttlMinutes = 10_080L; // 7일

        adapter.save(USER_ID, TOKEN_VALUE, ttlMinutes);

        verify(valueOperations, times(1))
                .set(KEY, TOKEN_VALUE, ttlMinutes, TimeUnit.MINUTES);
    }

    // -----------------------------------------------------------------------
    // T8-2: getAndDelete() — 존재하는 키 → 값 반환 + 키 삭제됨
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("getAndDelete()는 존재하는 키의 값을 반환하고 키를 삭제한다")
    void getAndDelete_existingKey_returnsValueAndDeletesKey() {
        when(valueOperations.getAndDelete(KEY)).thenReturn(TOKEN_VALUE);

        Optional<String> result = adapter.getAndDelete(USER_ID);

        assertThat(result).contains(TOKEN_VALUE);
        verify(valueOperations, times(1)).getAndDelete(KEY);
    }

    // -----------------------------------------------------------------------
    // T8-3: getAndDelete() — 존재하지 않는 키 → Optional.empty()
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("getAndDelete()는 존재하지 않는 키이면 Optional.empty()를 반환한다")
    void getAndDelete_nonExistentKey_returnsEmpty() {
        when(valueOperations.getAndDelete(KEY)).thenReturn(null);

        Optional<String> result = adapter.getAndDelete(USER_ID);

        assertThat(result).isEmpty();
        verify(valueOperations, times(1)).getAndDelete(KEY);
    }

    // -----------------------------------------------------------------------
    // T8-4: delete() → 키 제거됨
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("delete()는 'RT:{userId}' 키를 Redis에서 제거한다")
    void delete_removesKey() {
        adapter.delete(USER_ID);

        verify(redisTemplate, times(1)).delete(KEY);
    }

    // -----------------------------------------------------------------------
    // T8-5: save() 후 getAndDelete() — 키 prefix 'RT:' 일관성 검증
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("save()와 getAndDelete()는 동일한 'RT:{userId}' 키를 사용한다")
    void save_and_getAndDelete_useConsistentKey() {
        long ttlMinutes = 60L;
        when(valueOperations.getAndDelete(KEY)).thenReturn(TOKEN_VALUE);

        adapter.save(USER_ID, TOKEN_VALUE, ttlMinutes);
        Optional<String> result = adapter.getAndDelete(USER_ID);

        assertThat(result).contains(TOKEN_VALUE);
        verify(valueOperations).set(KEY, TOKEN_VALUE, ttlMinutes, TimeUnit.MINUTES);
        verify(valueOperations).getAndDelete(KEY);
    }

    // -----------------------------------------------------------------------
    // T8-6: 서로 다른 userId는 서로 다른 키를 사용한다
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("서로 다른 userId에 대해 각자 독립된 Redis 키를 사용한다")
    void save_differentUserIds_usesDifferentKeys() {
        Long anotherUserId = 99L;
        String anotherKey = "RT:" + anotherUserId;

        adapter.save(USER_ID, "token-a", 60L);
        adapter.save(anotherUserId, "token-b", 60L);

        verify(valueOperations).set(KEY, "token-a", 60L, TimeUnit.MINUTES);
        verify(valueOperations).set(anotherKey, "token-b", 60L, TimeUnit.MINUTES);
    }
}
