package dev.jazzybyte.onseoul.notification.application;

import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Clock;
import java.time.ZoneOffset;

/**
 * 시점 트리거 스케줄러가 "오늘" 을 결정할 때 사용하는 {@link Clock} 빈.
 *
 * <p>JVM 기본 타임존을 UTC 로 고정({@code OnSeoulApiApplication})하므로 UTC 고정 Clock 을 둔다.
 * 어댑터의 날짜 구간 {@code [d, d+1)} 도 UTC 기준이라 today 와 일관된다.
 * 테스트는 {@code Clock.fixed(...)} 로 today 를 주입한다.
 */
@Configuration
class NotificationClockConfig {

    @Bean
    @ConditionalOnMissingBean(Clock.class)
    Clock notificationClock() {
        return Clock.system(ZoneOffset.UTC);
    }
}
