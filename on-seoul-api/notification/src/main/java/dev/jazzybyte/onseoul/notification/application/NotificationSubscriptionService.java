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
 * 조건이 하나도 없는 빈 구독·키워드 한도 초과는 {@code INVALID_INPUT} 로 변환한다.
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
        Set<NotificationChannel> channels = requireNonEmptyChannels(cmd.channels());
        SubscriptionFilter filter = cmd.filter() != null ? cmd.filter() : SubscriptionFilter.empty();
        requireAtLeastOneCondition(filter);
        requireKeywordLimit(filter);

        NotificationSubscription subscription =
                NotificationSubscription.create(userId, filter, channels);

        try {
            NotificationSubscription saved = saveSubscriptionPort.insert(subscription);
            return toView(saved);
        } catch (DataIntegrityViolationException ex) {
            log.debug("[NotificationSubscription] 구독 INSERT 제약 위반: userId={}", userId);
            throw new OnSeoulApiException(ErrorCode.SUBSCRIPTION_CONFLICT);
        }
    }

    @Override
    @Transactional
    public SubscriptionView update(Long userId, Long subscriptionId, UpdateSubscriptionCommand cmd) {
        requireAnyUpdate(cmd);
        if (cmd.filter() != null) {
            requireAtLeastOneCondition(cmd.filter());
            requireKeywordLimit(cmd.filter());
        }
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

    /** 빈 구독 가드: 필터 조건이 모두 비어 있으면 거부 (전체 변경 구독 방지). */
    private void requireAtLeastOneCondition(SubscriptionFilter filter) {
        if (filter == null || filter.isEmpty()) {
            throw new OnSeoulApiException(ErrorCode.INVALID_INPUT,
                    "필터 조건(상태/지역/카테고리/키워드) 중 최소 1개는 지정해야 합니다.");
        }
    }

    /** 키워드 개수 제한 가드. */
    private void requireKeywordLimit(SubscriptionFilter filter) {
        if (filter != null && filter.keywords().size() > SubscriptionFilter.MAX_KEYWORDS) {
            throw new OnSeoulApiException(ErrorCode.INVALID_INPUT,
                    "키워드는 최대 " + SubscriptionFilter.MAX_KEYWORDS + "개까지 지정할 수 있습니다.");
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
