package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
class CreateDefaultSubscriptionsServiceTest {

    @Mock
    private SaveSubscriptionPort saveSubscriptionPort;

    private CreateDefaultSubscriptionsService service;

    @BeforeEach
    void setUp() {
        service = new CreateDefaultSubscriptionsService(saveSubscriptionPort);
    }

    @Test
    @DisplayName("create() — 5개 기본 서비스 ID에 대해 saveIfAbsent를 각 1회씩 호출한다")
    void create_callsSaveIfAbsentForEachDefaultServiceId() {
        service.create(42L);

        ArgumentCaptor<NotificationSubscription> captor =
                ArgumentCaptor.forClass(NotificationSubscription.class);
        verify(saveSubscriptionPort, times(5)).saveIfAbsent(captor.capture());

        List<String> capturedServiceIds = captor.getAllValues().stream()
                .map(NotificationSubscription::getServiceId)
                .toList();
        assertThat(capturedServiceIds).containsExactlyInAnyOrder(
                "OA-2269", "OA-2266", "OA-2267", "OA-2268", "OA-2270");
    }

    @Test
    @DisplayName("create() — 모든 구독의 userId는 전달받은 userId와 같다")
    void create_allSubscriptionsHaveCorrectUserId() {
        service.create(7L);

        ArgumentCaptor<NotificationSubscription> captor =
                ArgumentCaptor.forClass(NotificationSubscription.class);
        verify(saveSubscriptionPort, times(5)).saveIfAbsent(captor.capture());

        captor.getAllValues().forEach(sub ->
                assertThat(sub.getUserId()).isEqualTo(7L));
    }

    @Test
    @DisplayName("create() — 기본 구독의 채널은 EMAIL이다")
    void create_defaultChannelIsEmail() {
        service.create(10L);

        ArgumentCaptor<NotificationSubscription> captor =
                ArgumentCaptor.forClass(NotificationSubscription.class);
        verify(saveSubscriptionPort, times(5)).saveIfAbsent(captor.capture());

        captor.getAllValues().forEach(sub ->
                assertThat(sub.getChannels()).containsExactly(NotificationChannel.EMAIL));
    }
}
