package dev.jazzybyte.onseoul.adapter.out.redis;

import dev.jazzybyte.onseoul.domain.port.out.RefreshTokenStorePort;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

import java.util.Optional;
import java.util.concurrent.TimeUnit;

@Component
class RefreshTokenRedisAdapter implements RefreshTokenStorePort {

    private static final long TTL_DAYS = 7L;

    private final StringRedisTemplate redisTemplate;

    RefreshTokenRedisAdapter(final StringRedisTemplate redisTemplate) {
        this.redisTemplate = redisTemplate;
    }

    @Override
    public void save(Long userId, String refreshToken) {
        redisTemplate.opsForValue().set("RT:" + userId, refreshToken, TTL_DAYS, TimeUnit.DAYS);
    }

    @Override
    public Optional<String> find(Long userId) {
        return Optional.ofNullable(redisTemplate.opsForValue().get("RT:" + userId));
    }

    @Override
    public void delete(Long userId) {
        redisTemplate.delete("RT:" + userId);
    }
}
