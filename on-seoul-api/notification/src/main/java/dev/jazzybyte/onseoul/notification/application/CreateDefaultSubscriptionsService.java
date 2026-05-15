package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.port.in.CreateDefaultSubscriptionsUseCase;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Slf4j
@Service
@RequiredArgsConstructor
public class CreateDefaultSubscriptionsService implements CreateDefaultSubscriptionsUseCase {

    private static final List<String> DEFAULT_SERVICE_IDS = List.of(
            "OA-2269", "OA-2266", "OA-2267", "OA-2268", "OA-2270"
    );

    private final SaveSubscriptionPort saveSubscriptionPort;

    // 로그인 초기화 컨텍스트 — 5건이 한 TX에 묶이나, saveIfAbsent()가 INSERT ... ON CONFLICT DO NOTHING으로
    // DB 레벨에서 중복을 무시하므로 부분 실패 없음.
    @Override
    @Transactional
    public void create(Long userId) {
        DEFAULT_SERVICE_IDS.forEach(serviceId -> {
            NotificationSubscription subscription = NotificationSubscription.create(userId, serviceId);
            saveSubscriptionPort.saveIfAbsent(subscription);
        });
        log.info("[Notification] 기본 구독 생성 완료: userId={}, serviceIds={}", userId, DEFAULT_SERVICE_IDS);
    }
}
