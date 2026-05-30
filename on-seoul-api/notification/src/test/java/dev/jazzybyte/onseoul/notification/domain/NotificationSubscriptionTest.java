package dev.jazzybyte.onseoul.notification.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

class NotificationSubscriptionTest {

    @Test
    @DisplayName("create() — userId, channels로 초기 구독 생성 (serviceId 없음)")
    void create_initializesWithEmptyFilter() {
        NotificationSubscription sub = NotificationSubscription.create(1L,
                Set.of(NotificationChannel.EMAIL));

        assertThat(sub.getUserId()).isEqualTo(1L);
        assertThat(sub.getFilter()).isEqualTo("{}");
        assertThat(sub.getChannels()).containsExactly(NotificationChannel.EMAIL);
        assertThat(sub.getLastNotifiedAt()).isNull();
        assertThat(sub.getId()).isNull();
        assertThat(sub.getCreatedAt()).isNotNull();
    }

    @Test
    @DisplayName("create() — EMAIL+SMS 복수 채널 설정 가능")
    void create_withMultipleChannels() {
        NotificationSubscription sub = NotificationSubscription.create(2L,
                Set.of(NotificationChannel.EMAIL, NotificationChannel.SMS));

        assertThat(sub.getChannels()).containsExactlyInAnyOrder(
                NotificationChannel.EMAIL, NotificationChannel.SMS);
    }

    @Test
    @DisplayName("markNotified() — lastNotifiedAt 갱신")
    void markNotified_updatesLastNotifiedAt() {
        NotificationSubscription sub = NotificationSubscription.create(1L,
                Set.of(NotificationChannel.EMAIL));
        Instant now = Instant.now();

        sub.markNotified(now);

        assertThat(sub.getLastNotifiedAt()).isEqualTo(now);
    }
}
