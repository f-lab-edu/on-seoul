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
    @DisplayName("create(filter) — 비어있지 않은 필터는 parsedFilter에 보관되고 filter(JSON)은 null로 둔다")
    void create_withNonEmptyFilter_keepsParsedFilterAndNullJson() {
        SubscriptionFilter filter = new SubscriptionFilter(
                Set.of("RECEIVING"), Set.of("강남구"), Set.of(), Set.of("탁구"));

        NotificationSubscription sub = NotificationSubscription.create(3L, filter,
                Set.of(NotificationChannel.SMS));

        assertThat(sub.getParsedFilter()).isEqualTo(filter);
        // 비어있지 않은 필터는 어댑터가 INSERT 시 직렬화하므로 JSON 문자열은 미리 채우지 않는다
        assertThat(sub.getFilter()).isNull();
        assertThat(sub.getChannels()).containsExactly(NotificationChannel.SMS);
    }

    @Test
    @DisplayName("create(null filter) — null 필터는 빈 필터로 정규화되어 filter=\"{}\"")
    void create_withNullFilter_normalizesToEmpty() {
        NotificationSubscription sub = NotificationSubscription.create(4L, null,
                Set.of(NotificationChannel.EMAIL));

        assertThat(sub.getParsedFilter()).isNotNull();
        assertThat(sub.getParsedFilter().isEmpty()).isTrue();
        assertThat(sub.getFilter()).isEqualTo("{}");
    }

    @Test
    @DisplayName("create(빈 필터) — parsedFilter 보관 + filter=\"{}\" 둘 다 설정")
    void create_withEmptyFilter_setsBothRepresentations() {
        NotificationSubscription sub = NotificationSubscription.create(5L,
                SubscriptionFilter.empty(), Set.of(NotificationChannel.EMAIL));

        assertThat(sub.getParsedFilter()).isNotNull();
        assertThat(sub.getFilter()).isEqualTo("{}");
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
