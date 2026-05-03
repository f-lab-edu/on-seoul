package dev.jazzybyte.onseoul.adapter.out.redis;

import dev.jazzybyte.onseoul.domain.port.out.RefreshTokenStorePort;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.util.Optional;
import java.util.concurrent.TimeUnit;

@Component
class RefreshTokenRedisAdapter implements RefreshTokenStorePort {

    private static final String KEY_PREFIX = "RT:";

    private final StringRedisTemplate redisTemplate;

    RefreshTokenRedisAdapter(final StringRedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    @Override
    public void save(Long userId, String refreshToken, long ttlMinutes) {
        redisTemplate.opsForValue().set(KEY_PREFIX + userId, refreshToken, ttlMinutes, TimeUnit.MINUTES);
    }

    @Override
    public Optional<String> getAndDelete(Long userId) {
        String value = redisTemplate.opsForValue().getAndDelete(KEY_PREFIX + userId);
        return Optional.ofNullable(value);
    }

    @Override
    public void delete(Long userId) {
        redisTemplate.delete(KEY_PREFIX + userId);
    }
}
