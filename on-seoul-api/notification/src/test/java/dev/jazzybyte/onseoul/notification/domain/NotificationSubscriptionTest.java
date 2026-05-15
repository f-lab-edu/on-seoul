package dev.jazzybyte.onseoul.notification.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.Instant;

import static org.assertj.core.api.Assertions.assertThat;

class NotificationSubscriptionTest {

    @Test
    @DisplayName("create() — userId와 serviceId로 초기 구독 생성")
    void create_initializesWithEmptyFilter() {
        NotificationSubscription sub = NotificationSubscription.create(1L, "SVC-001");

        assertThat(sub.getUserId()).isEqualTo(1L);
        assertThat(sub.getServiceId()).isEqualTo("SVC-001");
        assertThat(sub.getFilter()).isEqualTo("{}");
        assertThat(sub.getLastNotifiedAt()).isNull();
        assertThat(sub.getId()).isNull();
        assertThat(sub.getCreatedAt()).isNotNull();
    }

    @Test
    @DisplayName("markNotified() — lastNotifiedAt 갱신")
    void markNotified_updatesLastNotifiedAt() {
        NotificationSubscription sub = NotificationSubscription.create(1L, "SVC-001");
        Instant now = Instant.now();

        sub.markNotified(now);

        assertThat(sub.getLastNotifiedAt()).isEqualTo(now);
    }
}
