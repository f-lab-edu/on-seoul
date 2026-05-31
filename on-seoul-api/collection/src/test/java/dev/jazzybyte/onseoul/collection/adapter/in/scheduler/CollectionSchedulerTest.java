package dev.jazzybyte.onseoul.collection.adapter.in.scheduler;

import dev.jazzybyte.onseoul.collection.port.in.CollectDatasetUseCase;
import dev.jazzybyte.onseoul.event.CollectionCompletedEvent;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.context.ApplicationEventPublisher;

import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.verify;

/**
 * QA 회귀 테스트: CollectionScheduler가 수집 완료 이벤트를 finally 블록에서 발행하는지 검증.
 *
 * <p>핵심 계약: 수집이 성공하든 실패하든(예외 전파) 항상
 * {@link CollectionCompletedEvent}가 발행되어야 한다. 성공한 소스에서 생긴
 * 변경분에 대한 알림은 보내야 하기 때문이다.
 */
@ExtendWith(MockitoExtension.class)
class CollectionSchedulerTest {

    @Mock
    private CollectDatasetUseCase collectDatasetUseCase;

    @Mock
    private ApplicationEventPublisher eventPublisher;

    @Test
    @DisplayName("수집 성공 시 collectAll 호출 후 CollectionCompletedEvent를 발행한다")
    void scheduledCollect_success_publishesEventAfterCollect() {
        CollectionScheduler scheduler = new CollectionScheduler(collectDatasetUseCase, eventPublisher);

        scheduler.scheduledCollect();

        InOrder order = inOrder(collectDatasetUseCase, eventPublisher);
        order.verify(collectDatasetUseCase).collectAll();
        order.verify(eventPublisher).publishEvent(org.mockito.ArgumentMatchers.any(CollectionCompletedEvent.class));
    }

    @Test
    @DisplayName("collectAll이 예외를 던져도 finally에서 CollectionCompletedEvent를 발행하고 예외를 전파한다")
    void scheduledCollect_collectAllThrows_stillPublishesEventAndRethrows() {
        RuntimeException collectError = new RuntimeException("일부 소스 수집 실패");
        doThrow(collectError).when(collectDatasetUseCase).collectAll();
        CollectionScheduler scheduler = new CollectionScheduler(collectDatasetUseCase, eventPublisher);

        assertThatThrownBy(scheduler::scheduledCollect).isSameAs(collectError);

        // finally 블록: 예외와 무관하게 이벤트 발행 보장
        verify(eventPublisher).publishEvent(org.mockito.ArgumentMatchers.any(CollectionCompletedEvent.class));
    }
}
