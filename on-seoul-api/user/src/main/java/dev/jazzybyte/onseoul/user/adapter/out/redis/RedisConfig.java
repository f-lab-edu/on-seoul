package dev.jazzybyte.onseoul.user.adapter.out.redis;

import io.lettuce.core.ClientOptions;
import io.lettuce.core.ClientOptions.DisconnectedBehavior;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.boot.autoconfigure.data.redis.LettuceClientConfigurationBuilderCustomizer;

import java.time.Duration;

/**
 * Lettuce Redis 클라이언트 커스텀 설정.
 *
 * <ul>
 *   <li>commandTimeout: 기본값 60s → 3s로 단축. Redis 장애 시 스레드 점유 최소화.</li>
 *   <li>disconnectedBehavior: REJECT_COMMANDS — 연결이 끊긴 동안 명령을 큐잉하지 않고
 *       즉시 예외를 반환한다. DataAccessException으로 전파되므로 호출부에서 명시적으로
 *       처리할 수 있다.</li>
 *   <li>autoReconnect: true — 연결 복구는 Lettuce가 자동으로 시도한다.</li>
 * </ul>
 */
@Configuration
public class RedisConfig {

    @Bean
    public LettuceClientConfigurationBuilderCustomizer lettuceClientConfigurationCustomizer() {
        return builder -> builder
                .commandTimeout(Duration.ofSeconds(3))
                .clientOptions(ClientOptions.builder()
                        .disconnectedBehavior(DisconnectedBehavior.REJECT_COMMANDS)
                        .autoReconnect(true)
                        .build());
    }
}
