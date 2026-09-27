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
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.scheduling.support.CronExpression;

import java.lang.reflect.Method;
import java.time.DayOfWeek;
import java.time.Duration;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;

import static org.assertj.core.api.Assertions.assertThat;
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

    /**
     * QA 회귀 가드: 수집 스케줄러는 주 1회 월요일 KST 08:00에 고정되어야 한다.
     *
     * <p>JVM 기본 타임존은 UTC로 강제되므로({@code OnSeoulApiApplication.init()}),
     * {@code @Scheduled}에 {@code zone="Asia/Seoul"}가 없으면 08:00 UTC(=17:00 KST)에 돈다.
     * 이 테스트는 cron과 zone 메타데이터를 직접 단정해 zone 누락 회귀를 막고,
     * cron 표현식이 실제로 "월요일에만" 발화하는지 {@link CronExpression}으로 검증한다.
     */
    @Test
    @DisplayName("scheduledCollect @Scheduled 어노테이션은 주 1회 월요일 cron(0 0 8 * * MON) + zone=Asia/Seoul 이어야 한다")
    void scheduledCollect_annotation_isPinnedToWeeklyMondayKst0800() throws NoSuchMethodException {
        Method method = CollectionScheduler.class.getDeclaredMethod("scheduledCollect");
        Scheduled scheduled = method.getAnnotation(Scheduled.class);

        assertThat(scheduled).as("@Scheduled 어노테이션이 존재해야 한다").isNotNull();
        assertThat(scheduled.cron()).as("주 1회 월요일 08:00 cron").isEqualTo("0 0 8 * * MON");
        assertThat(scheduled.zone())
                .as("JVM 기본 존(UTC)과 무관하게 한국시간 08:00 고정 — zone 누락 시 17:00 KST 회귀")
                .isEqualTo("Asia/Seoul");
    }

    /**
     * cron 문자열 단정만으로는 "주 1회 월요일"이라는 의도가 검증되지 않는다.
     * day-of-week 필드를 쓰면서 day-of-month 를 {@code *} 로 두는 조합이 Spring 에서
     * 실제로 월요일만 발화하는지(=OR 해석으로 매일 돌지 않는지) 파싱 결과로 확인한다.
     */
    @Test
    @DisplayName("수집 cron 은 KST 월요일 08:00에만 발화하며 다음 발화까지 정확히 7일 간격이다")
    void scheduledCollect_cron_firesOnlyOnMondayKst_withWeeklyInterval() throws NoSuchMethodException {
        Method method = CollectionScheduler.class.getDeclaredMethod("scheduledCollect");
        Scheduled scheduled = method.getAnnotation(Scheduled.class);
        ZoneId kst = ZoneId.of(scheduled.zone());

        CronExpression cron = CronExpression.parse(scheduled.cron());
        // 기준: 2026-06-03(수) 00:00 KST — 월요일이 아닌 임의의 요일에서 출발한다.
        ZonedDateTime fire = cron.next(ZonedDateTime.of(LocalDateTime.of(2026, 6, 3, 0, 0), kst));

        assertThat(fire).isNotNull();
        assertThat(fire.getDayOfWeek()).as("월요일에만 발화").isEqualTo(DayOfWeek.MONDAY);
        assertThat(fire.toLocalTime()).as("08:00 KST").isEqualTo(LocalTime.of(8, 0));
        assertThat(Duration.between(fire, cron.next(fire)).toDays())
                .as("주 1회 — 다음 발화까지 7일")
                .isEqualTo(7L);
    }
}
