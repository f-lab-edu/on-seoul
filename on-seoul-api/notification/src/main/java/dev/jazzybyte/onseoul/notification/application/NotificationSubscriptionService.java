package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.in.CreateSubscriptionUseCase;
import dev.jazzybyte.onseoul.notification.port.in.DeleteSubscriptionUseCase;
import dev.jazzybyte.onseoul.notification.port.in.ListSubscriptionsUseCase;
import dev.jazzybyte.onseoul.notification.port.in.UpdateSubscriptionUseCase;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.SubscriptionFilterParserPort;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Set;

/**
 * 알림 구독 관리 use case 집합 구현체.
 *
 * <p>권한 위반(다른 유저의 구독)은 {@code FORBIDDEN}, 미존재는 {@code SUBSCRIPTION_NOT_FOUND},
 * {@code (user_id, service_id)} 중복은 {@code SUBSCRIPTION_CONFLICT} 로 변환한다.
 *
 * <p>JSON 직렬화는 어댑터(persistence mapper) 책임이다. application 은 도메인 타입
 * ({@link SubscriptionFilter}, {@link NotificationChannel}) 만 다룬다.
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class NotificationSubscriptionService implements
        ListSubscriptionsUseCase,
        CreateSubscriptionUseCase,
        UpdateSubscriptionUseCase,
        DeleteSubscriptionUseCase {

    private final LoadSubscriptionPort loadSubscriptionPort;
    private final SaveSubscriptionPort saveSubscriptionPort;
    private final SubscriptionFilterParserPort filterParser;

    @Override
    @Transactional(readOnly = true)
    public List<SubscriptionView> list(Long userId) {
        return loadSubscriptionPort.loadByUserId(userId).stream()
                .map(this::toView)
                .toList();
    }

    @Override
    @Transactional
    public SubscriptionView create(Long userId, CreateSubscriptionCommand cmd) {
        requireServiceId(cmd.serviceId());
        Set<NotificationChannel> channels = requireNonEmptyChannels(cmd.channels());
        SubscriptionFilter filter = cmd.filter() != null ? cmd.filter() : SubscriptionFilter.empty();

        NotificationSubscription subscription =
                NotificationSubscription.create(userId, cmd.serviceId(), filter, channels);

        try {
            NotificationSubscription saved = saveSubscriptionPort.insert(subscription);
            return toView(saved);
        } catch (DataIntegrityViolationException ex) {
            log.debug("[NotificationSubscription] 중복 구독 INSERT 차단: userId={}, serviceId={}",
                    userId, cmd.serviceId());
            throw new OnSeoulApiException(ErrorCode.SUBSCRIPTION_CONFLICT);
        }
    }

    @Override
    @Transactional
    public SubscriptionView update(Long userId, Long subscriptionId, UpdateSubscriptionCommand cmd) {
        requireAnyUpdate(cmd);
        NotificationSubscription existing = loadOwned(userId, subscriptionId);

        Set<NotificationChannel> channels = cmd.channels();
        if (channels != null) {
            channels = requireNonEmptyChannels(channels);
        }
        NotificationSubscription updated =
                saveSubscriptionPort.updatePartial(existing.getId(), cmd.filter(), channels);
        return toView(updated);
    }

    @Override
    @Transactional
    public void delete(Long userId, Long subscriptionId) {
        NotificationSubscription existing = loadOwned(userId, subscriptionId);
        saveSubscriptionPort.deleteById(existing.getId());
    }

    // ── helpers ──────────────────────────────────────────────────────

    private SubscriptionView toView(NotificationSubscription s) {
        return new SubscriptionView(
                s.getId(),
                s.getUserId(),
                s.getServiceId(),
                filterParser.parse(s.getFilter()),
                s.getChannels(),
                s.getLastNotifiedAt(),
                s.getCreatedAt());
    }

    private NotificationSubscription loadOwned(Long userId, Long subscriptionId) {
        NotificationSubscription existing = loadSubscriptionPort.loadById(subscriptionId)
                .orElseThrow(() -> new OnSeoulApiException(ErrorCode.SUBSCRIPTION_NOT_FOUND));
        if (!existing.getUserId().equals(userId)) {
            throw new OnSeoulApiException(ErrorCode.FORBIDDEN);
        }
        return existing;
    }

    private void requireServiceId(String serviceId) {
        if (serviceId == null || serviceId.isBlank()) {
            throw new OnSeoulApiException(ErrorCode.INVALID_INPUT, "serviceId 는 필수입니다.");
        }
    }

    private Set<NotificationChannel> requireNonEmptyChannels(Set<NotificationChannel> channels) {
        if (channels == null || channels.isEmpty()) {
            throw new OnSeoulApiException(ErrorCode.INVALID_INPUT, "채널은 최소 1개 이상이어야 합니다.");
        }
        return channels;
    }

    private void requireAnyUpdate(UpdateSubscriptionCommand cmd) {
        if (cmd.filter() == null && cmd.channels() == null) {
            throw new OnSeoulApiException(ErrorCode.INVALID_INPUT,
                    "filter 또는 channels 중 하나는 반드시 제공되어야 합니다.");
        }
    }
}
