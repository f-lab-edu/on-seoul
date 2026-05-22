package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import dev.jazzybyte.onseoul.notification.port.out.SaveDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.SubscriptionFilterParserPort;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Optional;

/**
 * ADR-0004 per-batch 트랜잭션 헬퍼.
 *
 * <p>가상 스레드 풀에서 직접 {@link Transactional}이 동작하지 않으므로
 * Spring 프록시 빈으로 분리한다.
 */
@Component
@RequiredArgsConstructor
public class NotificationTxHelper {

    private final LoadServiceChangePort loadServiceChangePort;
    private final SaveDispatchPort saveDispatchPort;
    private final SaveSubscriptionPort saveSubscriptionPort;
    private final SubscriptionFilterParserPort subscriptionFilterParserPort;

    /**
     * TX A: subscription.filter를 SubscriptionFilter로 역직렬화한 뒤 lastNotifiedAt 이후의 변경을 조회한다.
     * 변경이 존재하면 (batch_id, subscription_id) UNIQUE 제약을 이용해 PENDING dispatch를 멱등 INSERT 한다.
     *
     * @return (변경 목록, 신규 INSERT된 dispatch). 변경이 없거나 dispatch가 이미 존재(중복 배치)면
     *         dispatch는 Optional.empty 이며 호출자는 발송을 건너뛰어야 한다.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public TxAResult txA(Long batchId, NotificationSubscription sub) {
        SubscriptionFilter filter = subscriptionFilterParserPort.parse(sub.getFilter());

        List<ServiceChange> changes = loadServiceChangePort.loadFiltered(
                sub.getServiceId(), filter, sub.getLastNotifiedAt());

        if (changes.isEmpty()) {
            return new TxAResult(List.of(), Optional.empty());
        }

        Optional<NotificationDispatch> dispatch = saveDispatchPort.saveIfAbsent(
                NotificationDispatch.create(batchId, sub.getId()));
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
     * TX B 실패: dispatch FAILED + last_error 갱신. last_notified_at은 갱신하지 않음.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void txBFailure(NotificationDispatch dispatch, String errorMessage) {
        dispatch.markFailed(errorMessage);
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
