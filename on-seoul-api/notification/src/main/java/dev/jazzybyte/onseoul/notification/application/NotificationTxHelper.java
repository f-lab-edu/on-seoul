package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import dev.jazzybyte.onseoul.notification.port.out.SaveDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.SubscriptionFilterParserPort;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;
import java.util.Optional;

/**
 * ADR-0004 per-batch 트랜잭션 헬퍼.
 *
 * <p>가상 스레드 풀에서 직접 {@link Transactional}이 동작하지 않으므로
 * Spring 프록시 빈으로 분리한다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class NotificationTxHelper {

    private final LoadServiceChangePort loadServiceChangePort;
    private final LoadDispatchPort loadDispatchPort;
    private final SaveDispatchPort saveDispatchPort;
    private final SaveSubscriptionPort saveSubscriptionPort;
    private final SubscriptionFilterParserPort subscriptionFilterParserPort;

    /**
     * TX A: subscription.filter를 SubscriptionFilter로 역직렬화한 뒤
     * (lastNotifiedAt, batch.startedAt] 범위의 변경을 조회한다.
     *
     * <p>상한({@code batch.startedAt})을 전달해 쿼리 시점까지의 변경이 이번 배치에 포함되는 것을 막는다.
     * {@code txBSuccess}가 커서를 {@code batch.startedAt}으로 전진시키므로 상한과 커서가 항상 일치한다.
     *
     * <p>변경이 존재하면 (batch_id, subscription_id) UNIQUE 제약을 이용해 PENDING dispatch를 멱등 INSERT 한다.
     *
     * @return (변경 목록, 신규 INSERT된 dispatch). 변경이 없거나 dispatch가 이미 존재(중복 배치)면
     *         dispatch는 Optional.empty 이며 호출자는 발송을 건너뛰어야 한다.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public TxAResult txA(NotificationBatch batch, NotificationSubscription sub) {
        // DEAD guard: 영구 실패로 판정된 구독은 이후 모든 배치에서도 건너뛴다.
        // 재시도 스케줄러가 attempt_count >= MAX_ATTEMPTS 후 DEAD로 전환한 뒤에도
        // 메인 배치가 매 tick 새 dispatch를 생성하는 것을 방지한다.
        if (loadDispatchPort.existsDeadDispatchBySubscriptionId(sub.getId())) {
            log.debug("[txA] 구독 subscriptionId={} — DEAD dispatch 존재, 발송 건너뜀", sub.getId());
            return new TxAResult(List.of(), Optional.empty());
        }

        SubscriptionFilter filter = subscriptionFilterParserPort.parse(sub.getFilter());

        List<ServiceChange> changes = loadServiceChangePort.loadFiltered(
                sub.getServiceId(), filter, sub.getLastNotifiedAt(), batch.getStartedAt());

        if (changes.isEmpty()) {
            return new TxAResult(List.of(), Optional.empty());
        }

        Optional<NotificationDispatch> dispatch = saveDispatchPort.saveIfAbsent(
                NotificationDispatch.create(batch.getId(), sub.getId()));
        return new TxAResult(changes, dispatch);
    }

    /**
     * TX B 성공: dispatch SUCCESS 갱신 + subscription.last_notified_at = batch.startedAt 으로 전진.
     * ADR-0004: batch.startedAt을 커서로 사용 — 이 배치 시작 시각까지의 변경은 모두 처리됨.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void txBSuccess(NotificationDispatch dispatch, NotificationSubscription sub,
                           NotificationBatch batch,
                           String title, String body, TemplateSource source) {
        dispatch.markSuccess(title, body, source);
        saveDispatchPort.save(dispatch);

        sub.markNotified(batch.getStartedAt());
        saveSubscriptionPort.save(sub);
    }

    /**
     * TX B 실패: dispatch FAILED + title/body/source + last_error 갱신.
     * title/body/source를 저장하여 {@link DispatchRetryScheduler}가 재사용할 수 있게 한다.
     * last_notified_at은 갱신하지 않음.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void txBFailure(NotificationDispatch dispatch,
                           String generatedTitle, String generatedBody, TemplateSource templateSource,
                           String errorMessage) {
        dispatch.markFailed(errorMessage, generatedTitle, generatedBody, templateSource);
        saveDispatchPort.save(dispatch);
    }

    /**
     * Retry TX 성공: dispatch SUCCESS + last_notified_at = retryStartedAt 으로 전진.
     *
     * <p>메인 배치의 {@code batch.startedAt}과 동일한 방식으로, 처리 시작 시각을 커서로 사용한다.
     * TX 내부에서 {@code Instant.now()}를 호출하면 커밋 시각에 따라 편차가 생기므로
     * 호출자가 캡처한 시각을 인자로 받는다.
     *
     * @param retryStartedAt 재시도 루프 시작 시각 (호출자 캡처)
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void txBRetrySuccess(NotificationDispatch dispatch, NotificationSubscription sub,
                                Instant retryStartedAt) {
        dispatch.markSuccess(dispatch.getGeneratedTitle(), dispatch.getGeneratedBody(),
                dispatch.getTemplateSource());
        saveDispatchPort.save(dispatch);

        sub.markNotified(retryStartedAt);
        saveSubscriptionPort.save(sub);
    }

    /**
     * Retry TX 실패: dispatch 상태(FAILED 또는 DEAD + attempt_count)를 저장.
     * 호출 전에 도메인 메서드(markDead 또는 markFailed + incrementAttemptCount)를 호출해야 한다.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void txBRetryFailure(NotificationDispatch dispatch) {
        saveDispatchPort.save(dispatch);
    }

    /**
     * TX A 결과 묶음.
     *
     * @param changes  필터에 매칭된 변경 목록 (빈 경우 발송 생략)
     * @param dispatch saveIfAbsent로 새로 INSERT 된 dispatch. 이미 존재(중복 배치) 시 empty.
     */
    public record TxAResult(List<ServiceChange> changes, Optional<NotificationDispatch> dispatch) {}
}
