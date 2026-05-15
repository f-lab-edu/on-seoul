package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.ServiceChange;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import dev.jazzybyte.onseoul.notification.port.out.SaveDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;

/**
 * 트랜잭션 경계를 담당하는 헬퍼 빈.
 * NotificationScheduler(가상 스레드)에서 직접 @Transactional이 동작하지 않으므로
 * 별도 Spring 프록시 빈으로 분리한다.
 */
@Component
@RequiredArgsConstructor
public class NotificationTxHelper {

    private final LoadServiceChangePort loadServiceChangePort;
    private final SaveDispatchPort saveDispatchPort;
    private final SaveSubscriptionPort saveSubscriptionPort;

    /**
     * TX A: since 이후 변경 이력을 조회하고, 각 변경에 대해 dispatch 행을 saveIfAbsent로 선점한다.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public List<ServiceChange> txA(NotificationSubscription sub, int maxAttempts) {
        List<ServiceChange> changes = loadServiceChangePort.loadSince(
                sub.getServiceId(), sub.getLastNotifiedAt());

        for (ServiceChange change : changes) {
            saveDispatchPort.saveIfAbsent(
                    NotificationDispatch.create(sub.getId(), change.id()));
        }
        return changes;
    }

    /**
     * TX B 성공: dispatch를 SUCCESS로 갱신하고 subscription의 last_notified_at을 진전시킨다.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void txBSuccess(NotificationDispatch dispatch, NotificationSubscription sub,
                           ServiceChange change, String title, String body, TemplateSource source) {
        dispatch.markSuccess(title, body, source);
        saveDispatchPort.save(dispatch);

        Instant newNotifiedAt = sub.getLastNotifiedAt() == null
                ? change.changedAt()
                : change.changedAt().isAfter(sub.getLastNotifiedAt())
                        ? change.changedAt()
                        : sub.getLastNotifiedAt();
        sub.markNotified(newNotifiedAt);
        saveSubscriptionPort.save(sub);
    }

    /**
     * TX B 실패: dispatch를 FAILED(또는 DEAD)로 갱신한다. last_notified_at은 갱신하지 않는다.
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void txBFailure(NotificationDispatch dispatch, String errorMessage, int maxAttempts) {
        dispatch.markFailed(errorMessage, maxAttempts);
        saveDispatchPort.save(dispatch);
    }
}
