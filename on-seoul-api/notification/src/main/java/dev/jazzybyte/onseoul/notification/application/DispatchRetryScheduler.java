package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.notification.port.out.NotificationContentSerializerPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * FAILED dispatch 재시도 스케줄러 (outbox retry 패턴).
 *
 * <p>1시간 주기로 구독당 가장 최근 FAILED dispatch를 조회하여
 * 이미 생성된 generated_title/generated_body로 Knock 재발송을 시도한다.
 * AI 호출 없이 기존 메시지를 재사용하므로 Template 생성 비용이 없다.
 *
 * <p>재시도 정책:
 * <ul>
 *   <li>attempt_count < 5인 FAILED dispatch만 대상</li>
 *   <li>성공 시: SUCCESS + last_notified_at 전진 (now() 기준)</li>
 *   <li>실패 시: attempt_count++ + FAILED 유지</li>
 *   <li>attempt_count == 5 도달 시: DEAD 전환, last_notified_at 미갱신</li>
 * </ul>
 */
@Slf4j
@Component
public class DispatchRetryScheduler {

    private final LoadDispatchPort loadDispatchPort;
    private final LoadSubscriptionPort loadSubscriptionPort;
    private final LoadUserContactPort loadUserContactPort;
    private final PushNotificationPort pushNotificationPort;
    private final NotificationTxHelper txHelper;
    private final NotificationContentSerializerPort contentSerializer;

    /**
     * FAILED dispatch staleness 임계값. createdAt 으로부터 이 기간을 초과하면 재시도하지 않고
     * EXPIRED 로 폐기한다(저장된 콘텐츠가 stale 할 수 있어 재발송보다 안전). 기본 12h.
     */
    private final Duration maxAge;
    private final int maxAgeHours;

    public DispatchRetryScheduler(
            final LoadDispatchPort loadDispatchPort,
            final LoadSubscriptionPort loadSubscriptionPort,
            final LoadUserContactPort loadUserContactPort,
            final PushNotificationPort pushNotificationPort,
            final NotificationTxHelper txHelper,
            final NotificationContentSerializerPort contentSerializer,
            @Value("${notification.retry-scheduler.max-age-hours:12}") final int maxAgeHours) {
        this.loadDispatchPort = loadDispatchPort;
        this.loadSubscriptionPort = loadSubscriptionPort;
        this.loadUserContactPort = loadUserContactPort;
        this.pushNotificationPort = pushNotificationPort;
        this.txHelper = txHelper;
        this.contentSerializer = contentSerializer;
        this.maxAgeHours = maxAgeHours;
        this.maxAge = Duration.ofHours(maxAgeHours);
    }

    @Scheduled(fixedDelayString = "${notification.retry-scheduler.fixed-delay-ms:3600000}")
    public void retryFailedDispatches() {
        log.debug("[DispatchRetryScheduler] 재시도 스케줄 실행 시작");

        List<NotificationDispatch> retryable;
        try {
            retryable = loadDispatchPort.findRetryable();
        } catch (Exception e) {
            log.error("[DispatchRetryScheduler] 재시도 대상 조회 실패: {}", e.getMessage(), e);
            return;
        }

        if (retryable.isEmpty()) {
            log.debug("[DispatchRetryScheduler] 재시도 대상 없음");
            return;
        }

        log.info("[DispatchRetryScheduler] 재시도 대상 {}건 처리 시작", retryable.size());

        Instant retryStartedAt = Instant.now();
        for (NotificationDispatch dispatch : retryable) {
            retryOne(dispatch, retryStartedAt);
        }

        log.info("[DispatchRetryScheduler] 재시도 스케줄 완료");
    }

    private void retryOne(NotificationDispatch dispatch, Instant retryStartedAt) {
        Long subscriptionId = dispatch.getSubscriptionId();

        // staleness 가드: createdAt 기준 max-age 초과면 send/구독 로드 이전에 EXPIRED 로 폐기한다.
        // findRetryable(status=FAILED) 가 다시 잡지 않으므로 영원한 FAILED(limbo)를 방지한다.
        // DEAD(재시도 소진)와 EXPIRED(max-age 초과)는 둘 중 먼저 닿는 조건이 종료를 결정한다.
        if (dispatch.isOlderThan(retryStartedAt, maxAge)) {
            dispatch.markExpired("max-age(" + maxAgeHours + "h) 초과 — stale 폐기");
            try {
                txHelper.txBRetryExpired(dispatch);
            } catch (Exception e) {
                log.error("[DispatchRetryScheduler] TX RetryExpired 실패: dispatchId={}, error={}",
                        dispatch.getId(), e.getMessage());
            }
            log.info("[DispatchRetryScheduler] EXPIRED 전환(stale): dispatchId={}, subscriptionId={}, ageHours>{}",
                    dispatch.getId(), subscriptionId, maxAgeHours);
            return;
        }

        Optional<NotificationSubscription> subOpt;
        try {
            subOpt = loadSubscriptionPort.loadById(subscriptionId);
        } catch (Exception e) {
            log.warn("[DispatchRetryScheduler] 구독 조회 실패 — 스킵: subscriptionId={}, error={}",
                    subscriptionId, e.getMessage());
            return;
        }

        if (subOpt.isEmpty()) {
            log.warn("[DispatchRetryScheduler] 구독 없음(삭제된 구독) — 스킵: subscriptionId={}", subscriptionId);
            return;
        }

        NotificationSubscription sub = subOpt.get();

        UserContact recipient = loadUserContactPort.loadContact(sub.getUserId())
                .orElseGet(() -> {
                    log.warn("[DispatchRetryScheduler] 연락처 미등록 — userId만으로 발송 시도: userId={}",
                            sub.getUserId());
                    return new UserContact(sub.getUserId(), null, null);
                });

        // notification_payload != null → 저장된 구조화 콘텐츠를 복원하여 무손실 재발송.
        // null(09 마이그레이션 이전 row 또는 직렬화 실패) → generated_title/body 평문 폴백.
        NotificationContent content = resolveContent(dispatch);

        try {
            pushNotificationPort.send(
                    recipient,
                    content,
                    dispatch.getId(),
                    sub.getChannels());

            try {
                txHelper.txBRetrySuccess(dispatch, sub, retryStartedAt);
            } catch (Exception e) {
                log.error("[DispatchRetryScheduler] TX RetrySuccess 실패: dispatchId={}, error={}",
                        dispatch.getId(), e.getMessage());
            }

            log.info("[DispatchRetryScheduler] 재시도 성공: dispatchId={}, subscriptionId={}",
                    dispatch.getId(), subscriptionId);

        } catch (RuntimeException pushEx) {
            dispatch.incrementAttemptCount();

            if (dispatch.isRetryExhausted()) {
                dispatch.markDead(pushEx.getMessage());
                log.warn("[DispatchRetryScheduler] DEAD 전환: dispatchId={}, attemptCount={}",
                        dispatch.getId(), dispatch.getAttemptCount());
            } else {
                // attempt_count만 증가시키고 기존 title/body/source는 그대로 유지
                dispatch.markFailed(pushEx.getMessage(),
                        dispatch.getGeneratedTitle(), dispatch.getGeneratedBody(), dispatch.getTemplateSource());
                log.warn("[DispatchRetryScheduler] 재시도 실패 — FAILED 유지: dispatchId={}, attemptCount={}",
                        dispatch.getId(), dispatch.getAttemptCount());
            }

            try {
                txHelper.txBRetryFailure(dispatch);
            } catch (Exception e) {
                log.error("[DispatchRetryScheduler] TX RetryFailure 실패: dispatchId={}, error={}",
                        dispatch.getId(), e.getMessage());
            }
        }
    }

    /**
     * 저장된 notification_payload(JSON)를 {@link NotificationContent}로 복원한다.
     * payload가 null/blank/파싱 실패면 generated_title/generated_body로 최소 콘텐츠를 구성한다
     * (구조화 카드 없는 평문 폴백 — 09 마이그레이션 이전 row 호환).
     */
    private NotificationContent resolveContent(NotificationDispatch dispatch) {
        NotificationContent restored = contentSerializer.deserialize(dispatch.getNotificationPayload());
        if (restored != null) {
            return restored;
        }
        return new NotificationContent(
                dispatch.getGeneratedTitle(), dispatch.getGeneratedBody(), List.of());
    }
}
