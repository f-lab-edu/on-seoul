package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.notification.adapter.out.persistence.NotificationContentSerializer;
import dev.jazzybyte.onseoul.notification.domain.BatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationBatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.ScheduledServiceMatch;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import dev.jazzybyte.onseoul.notification.domain.TriggerType;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadScheduledTriggerPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SubscriptionFilterParserPort;
import dev.jazzybyte.onseoul.notification.port.out.TemplateGenerationPort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.scheduling.support.CronExpression;

import java.lang.reflect.Method;
import java.time.Clock;
import java.time.DayOfWeek;
import java.time.Duration;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.List;
import java.util.Optional;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ScheduledTriggerSchedulerTest {

    @Mock private LoadSubscriptionPort loadSubscriptionPort;
    @Mock private LoadScheduledTriggerPort loadScheduledTriggerPort;
    @Mock private LoadDispatchPort loadDispatchPort;
    @Mock private SubscriptionFilterParserPort filterParser;
    @Mock private LoadUserContactPort loadUserContactPort;
    @Mock private TemplateGenerationPort templateGenerationPort;
    @Mock private PushNotificationPort pushNotificationPort;
    @Mock private SaveBatchPort saveBatchPort;
    @Mock private NotificationTxHelper txHelper;

    private ScheduledTriggerScheduler scheduler;

    private static final LocalDate TODAY = LocalDate.of(2026, 6, 3);
    private static final UserContact CONTACT = new UserContact(1L, "u@example.com", "+8210");

    @BeforeEach
    void setUp() {
        scheduler = new ScheduledTriggerScheduler(
                loadSubscriptionPort, loadScheduledTriggerPort, loadDispatchPort, filterParser,
                loadUserContactPort, templateGenerationPort, pushNotificationPort,
                new NotificationContentSerializer(new com.fasterxml.jackson.databind.ObjectMapper()),
                saveBatchPort, txHelper, Clock.systemUTC());

        lenient().when(filterParser.parse(any())).thenReturn(SubscriptionFilter.empty());
        // 기본: 두 dedup 선조회 모두 미존재(발행 진행).
        lenient().when(loadDispatchPort.existsChangeDispatchForServiceToday(anyLong(), any(), any()))
                .thenReturn(false);
        lenient().when(loadDispatchPort.existsScheduledDispatch(anyLong(), any(), any()))
                .thenReturn(false);
        lenient().when(loadUserContactPort.loadContact(anyLong())).thenReturn(Optional.of(CONTACT));
        lenient().when(loadScheduledTriggerPort.loadOpeningToday(any(), any())).thenReturn(List.of());
        lenient().when(loadScheduledTriggerPort.loadReceiptStartTomorrow(any(), any())).thenReturn(List.of());
        lenient().when(loadScheduledTriggerPort.loadDeadlineToday(any(), any())).thenReturn(List.of());
        lenient().when(loadSubscriptionPort.loadChunk(anyLong(), anyInt())).thenReturn(List.of());
        // dispatchOne 의 생존 재확인 기본값 — 구독이 살아 있는 상태. 해지 케이스는 개별 테스트에서 덮어쓴다.
        // 인자 id 를 반영해 돌려준다 — 고정 인스턴스를 반환하면 나중에 이 fresh read 를 실제로
        // 소비하게 만들었을 때 잘못된 구독이 흘러들어도 테스트가 초록으로 남는다.
        lenient().when(loadSubscriptionPort.loadById(anyLong()))
                .thenAnswer(inv -> Optional.of(sub(inv.getArgument(0, Long.class))));
        lenient().when(saveBatchPort.insertRunning(any())).thenAnswer(inv ->
                new NotificationBatch(7L, java.time.Instant.now(), null, BatchStatus.RUNNING, null, null));
        lenient().when(saveBatchPort.update(any())).thenAnswer(inv -> inv.getArgument(0));
        lenient().when(templateGenerationPort.generate(any()))
                .thenReturn(new TemplateResult("제목", "요약", TemplateSource.AI));
        // saveScheduledIfAbsent: 기본은 고정 PENDING dispatch 반환(첫 발행).
        // 인자를 echo 하지 않는다 — re-stub 시 lambda 가 null 인자로 재실행되어 NPE 나는 것을 피한다.
        lenient().when(txHelper.saveScheduledIfAbsent(any())).thenReturn(Optional.of(
                NotificationDispatch.createScheduled(7L, 1L, TriggerType.DEADLINE_DDAY, "OA-default", TODAY)));
    }

    private NotificationSubscription sub(long id) {
        return NotificationSubscription.ofPersistence(id, 1L, "{}",
                Set.of(NotificationChannel.EMAIL), null, java.time.Instant.now());
    }

    private ScheduledServiceMatch match(String serviceId) {
        return new ScheduledServiceMatch(serviceId, serviceId + "-name", null, null,
                null, "강남구", "접수중", null, null, "2026-06-03T00:00Z");
    }

    @Test
    @DisplayName("동시성 가드: 스케줄 run()이 실행 중이면 runManually()는 processAll 재진입 없이 SKIPPED")
    void runManually_whileScheduledRunning_skipsWithoutReentry() throws Exception {
        // run() 진입점이 보유한 running 플래그를 수동 호출이 공유하는지 검증한다.
        // 첫 스레드를 loadChunk(0,*) 안에서 latch로 블로킹하여 running=true 상태를 유지한다.
        java.util.concurrent.CountDownLatch entered = new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.CountDownLatch release = new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.atomic.AtomicInteger loadChunkCalls = new java.util.concurrent.atomic.AtomicInteger();

        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenAnswer(inv -> {
            loadChunkCalls.incrementAndGet();
            entered.countDown();
            release.await(5, java.util.concurrent.TimeUnit.SECONDS);
            return List.of();
        });

        Thread scheduled = new Thread(scheduler::run, "scheduled-run");
        scheduled.start();
        assertThat(entered.await(5, java.util.concurrent.TimeUnit.SECONDS)).isTrue();

        // run()이 running 플래그를 잡고 processAll 내부에 머무는 동안 수동 호출 시도
        ScheduledTriggerScheduler.ManualRunResult result = scheduler.runManually();

        assertThat(result).isEqualTo(ScheduledTriggerScheduler.ManualRunResult.SKIPPED_ALREADY_RUNNING);
        // 가드 우회로 인한 processAll 재진입(=두 번째 loadChunk(0,*))이 없어야 한다.
        assertThat(loadChunkCalls.get()).isEqualTo(1);

        release.countDown();
        scheduled.join(5_000);

        // 첫 배치 종료 후 플래그가 해제되어 다음 수동 실행은 RAN 으로 진입 가능
        assertThat(scheduler.runManually())
                .isEqualTo(ScheduledTriggerScheduler.ManualRunResult.RAN);
        assertThat(loadChunkCalls.get()).isEqualTo(2);
    }

    @Test
    @DisplayName("매칭 service마다 시점 dispatch를 발행하고 푸시 발송한다")
    void dispatchesPerMatchedService() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1"), match("OA-2")));

        ScheduledTriggerScheduler.RunResult result = scheduler.processAll(TODAY);

        verify(pushNotificationPort, org.mockito.Mockito.times(2))
                .send(any(UserContact.class), any(NotificationContent.class), any(), any());
        verify(txHelper, org.mockito.Mockito.times(2))
                .txBScheduledSuccess(any(), eq("제목"), eq("요약"), eq(TemplateSource.AI));
        // 집계: 두 service 모두 발송 → sent==2, skipped==0.
        assertThat(result.sent()).isEqualTo(2);
        assertThat(result.skipped()).isZero();
    }

    @Test
    @DisplayName("dedup으로 saveScheduledIfAbsent가 empty면 발송하지 않는다")
    void dedupSkipped_noSend() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1")));
        when(txHelper.saveScheduledIfAbsent(any())).thenReturn(Optional.empty());

        scheduler.processAll(TODAY);

        verifyNoInteractions(pushNotificationPort, templateGenerationPort);
        verify(txHelper, never()).txBScheduledSuccess(any(), any(), any(), any());
    }

    @Test
    @DisplayName("배치 진행 중 해지된 구독 → 시점 발행 skip (dedup 선조회·batch·AI·푸시 전부 생략)")
    void subscriptionDeletedMidBatch_skipsWithoutDispatch() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1")));
        when(loadSubscriptionPort.loadById(1L)).thenReturn(Optional.empty());

        ScheduledTriggerScheduler.RunResult result = scheduler.processAll(TODAY);

        // 해지된 구독에는 dispatch INSERT·AI 템플릿·푸시가 전부 발생하지 않는다.
        verifyNoInteractions(loadDispatchPort, saveBatchPort, templateGenerationPort,
                pushNotificationPort, loadUserContactPort);
        verify(txHelper, never()).saveScheduledIfAbsent(any());
        assertThat(result.sent()).isZero();
        assertThat(result.skipped()).isEqualTo(1);
    }

    @Test
    @DisplayName("CHANGE cross-dedup 히트 → 시점 발행 skip (batch 미생성, 발송 없음)")
    void crossDedupHit_skipsWithoutBatch() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1")));
        when(loadDispatchPort.existsChangeDispatchForServiceToday(eq(1L), eq("OA-1"), eq(TODAY)))
                .thenReturn(true);

        ScheduledTriggerScheduler.RunResult result = scheduler.processAll(TODAY);

        // batch 를 만들지 않고, dispatch 발행/발송도 하지 않는다(빈 batch 미생성).
        verifyNoInteractions(pushNotificationPort, templateGenerationPort);
        verify(saveBatchPort, never()).insertRunning(any());
        verify(txHelper, never()).saveScheduledIfAbsent(any());
        // 집계: cross-dedup 히트는 skipped==1, sent==0 으로 잡힌다.
        assertThat(result.skipped()).isEqualTo(1);
        assertThat(result.sent()).isZero();
    }

    @Test
    @DisplayName("시점-시점 dedup 선조회 히트 → 시점 발행 skip (batch 미생성)")
    void scheduledDedupHit_skipsWithoutBatch() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1")));
        when(loadDispatchPort.existsScheduledDispatch(eq(1L), eq("OA-1"), eq(TODAY)))
                .thenReturn(true);

        ScheduledTriggerScheduler.RunResult result = scheduler.processAll(TODAY);

        verifyNoInteractions(pushNotificationPort, templateGenerationPort);
        verify(saveBatchPort, never()).insertRunning(any());
        verify(txHelper, never()).saveScheduledIfAbsent(any());
        // 집계: 시점-시점 dedup 선조회 히트도 skipped==1, sent==0.
        assertThat(result.skipped()).isEqualTo(1);
        assertThat(result.sent()).isZero();
    }

    @Test
    @DisplayName("trigger_type이 템플릿 요청에 전달된다 (DEADLINE_DDAY)")
    void triggerTypePassedToTemplateRequest() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1")));

        scheduler.processAll(TODAY);

        ArgumentCaptor<NotificationTemplateRequest> captor =
                ArgumentCaptor.forClass(NotificationTemplateRequest.class);
        verify(templateGenerationPort).generate(captor.capture());
        assertThat(captor.getValue().triggerType()).isEqualTo(TriggerType.DEADLINE_DDAY);
        // 시점 트리거는 changes 빈 배열
        assertThat(captor.getValue().services().get(0).changes()).isEmpty();
    }

    @Test
    @DisplayName("push 실패 시 txBScheduledFailure 호출 (발송 카운트 미증가)")
    void pushFails_callsScheduledFailure() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-1")));
        doThrow(new RuntimeException("Knock 오류")).when(pushNotificationPort)
                .send(any(), any(NotificationContent.class), any(), any());

        scheduler.processAll(TODAY);

        verify(txHelper).txBScheduledFailure(any(), eq("제목"), eq("요약"), eq(TemplateSource.AI), eq("Knock 오류"));
    }

    @Test
    @DisplayName("세 트리거(개시/D-1/마감)를 모두 조회한다 — status 필터 무시는 어댑터 책임이므로 호출만 검증")
    void allThreeTriggersQueried() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));

        scheduler.processAll(TODAY);

        verify(loadScheduledTriggerPort).loadOpeningToday(any(), eq(TODAY));
        verify(loadScheduledTriggerPort).loadReceiptStartTomorrow(any(), eq(TODAY));
        verify(loadScheduledTriggerPort).loadDeadlineToday(any(), eq(TODAY));
    }

    @Test
    @DisplayName("한 구독 처리 실패는 삼켜지고 다음 구독은 정상 처리된다")
    void subscriptionFailureSwallowed_continues() {
        NotificationSubscription s1 = sub(1L);
        NotificationSubscription s2 = sub(2L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s1, s2));
        // s1 처리 중 filterParser 예외
        when(filterParser.parse(any()))
                .thenThrow(new RuntimeException("파싱 실패"))
                .thenReturn(SubscriptionFilter.empty());
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY)))
                .thenReturn(List.of(match("OA-2")));

        scheduler.processAll(TODAY);

        // s2는 정상 발송
        verify(pushNotificationPort).send(any(), any(NotificationContent.class), any(), any());
    }

    @Test
    @DisplayName("dispatch에 직렬화 payload가 할당되어 발송된다")
    void payloadAssigned() {
        NotificationSubscription s = sub(1L);
        when(loadSubscriptionPort.loadChunk(eq(0L), anyInt())).thenReturn(List.of(s));
        ScheduledServiceMatch m = match("OA-1");
        NotificationDispatch persisted = NotificationDispatch.createScheduled(
                7L, 1L, TriggerType.DEADLINE_DDAY, "OA-1", TODAY);
        when(txHelper.saveScheduledIfAbsent(any())).thenReturn(Optional.of(persisted));
        when(loadScheduledTriggerPort.loadDeadlineToday(any(), eq(TODAY))).thenReturn(List.of(m));

        scheduler.processAll(TODAY);

        assertThat(persisted.getNotificationPayload()).isNotNull();
        assertThat(persisted.getNotificationPayload()).contains("\"services\"");
    }

    // ── cross-dedup 실행 순서 불변식(회귀 가드) ──────────────────────────────
    // 수집이 주 1회(월요일 08:00 KST)로 바뀐 뒤에도 cross-dedup 선후 관계가 유지되는지 코드로 고정한다.
    // 불변식: "수집이 도는 주기"에는 수집(CHANGE 배치가 dispatch 선점) → 시점 트리거 순으로 실행된다.
    //   - JVM 기본 타임존은 UTC로 강제됨(OnSeoulApiApplication.init()).
    //   - 수집  : @Scheduled(cron="0 0 8 * * MON", zone="Asia/Seoul") → 월 08:00 KST = 일 23:00 UTC.
    //   - 시점  : @Scheduled(cron="...0 30 9 * * *", zone 미지정) → JVM UTC 기준 매일 09:30 UTC.
    // 수집 발화(일 23:00 UTC) 직후 처음 도는 시점 트리거는 월 09:30 UTC이므로, 수집이 약 10.5시간
    // 마진으로 선행한다(주 1회 전환 전 일간 편성과 동일한 마진 — 발화 요일만 줄었다).
    // 수집이 없는 나머지 요일에는 CHANGE 배치 자체가 생기지 않으므로 시점 트리거가 선점할
    // CHANGE dispatch 가 없다 → cross-dedup 경합 자체가 발생하지 않는다.
    // 이 마진이 무너지면 시점 트리거가 CHANGE보다 먼저 dispatch를 선점해 cross-dedup(CHANGE 우선)이 깨진다.

    @Test
    @DisplayName("시점 트리거 @Scheduled는 09:30 cron이며 zone 미지정(JVM UTC 기본)이다")
    void scheduledTrigger_annotation_isNinethirtyNoExplicitZone() throws NoSuchMethodException {
        Method run = ScheduledTriggerScheduler.class.getDeclaredMethod("run");
        Scheduled scheduled = run.getAnnotation(Scheduled.class);

        assertThat(scheduled).isNotNull();
        // 기본 cron 표현식(프로퍼티 미설정 시): 0 30 9 * * *
        assertThat(scheduled.cron()).isEqualTo("${notification.scheduled-trigger.cron:0 30 9 * * *}");
        // zone 미지정 → JVM 기본 타임존(UTC)을 따른다. zone 추가는 시점 트리거를 KST로 당겨
        // 수집(월 08:00 KST = 일 23:00 UTC)보다 앞당겨 cross-dedup 순서를 깰 수 있으므로 의도적으로 비워둔다.
        assertThat(scheduled.zone()).isEmpty();
    }

    @Test
    @DisplayName("주 1회 수집(월 08:00 KST = 일 23:00 UTC)은 직후 시점 트리거(월 09:30 UTC)보다 선행한다 — cross-dedup 순서 불변식")
    void weeklyCollectionMonday_precedesNextScheduledTrigger_crossDedupOrdering() {
        // 주의: 이 테스트는 기본 cron(0 30 9 / 0 0 8 ... MON)에 대한 *코드 회귀* 가드다.
        // 시점 트리거 cron은 notification.scheduled-trigger.cron 프로퍼티로 런타임 오버라이드 가능하므로,
        // 운영에서 트리거 시각을 앞당기면 이 테스트는 그대로 통과해도 순서가 깨질 수 있다(런타임 cron 미검증).
        ZoneId utc = ZoneOffset.UTC;
        ZoneId kst = ZoneId.of("Asia/Seoul");

        CronExpression collectCron = CronExpression.parse("0 0 8 * * MON");   // CollectionScheduler (zone=Asia/Seoul)
        CronExpression triggerCron = CronExpression.parse("0 30 9 * * *");    // ScheduledTriggerScheduler (zone 미지정 = UTC)

        // 임의의 비-월요일(2026-06-03 수) 기준으로 다음 수집 발화를 구한다.
        ZonedDateTime collectFireKst =
                collectCron.next(ZonedDateTime.of(LocalDateTime.of(2026, 6, 3, 0, 0), kst));
        ZonedDateTime collectFireUtc = collectFireKst.withZoneSameInstant(utc);

        // 수집은 KST 월요일 08:00 = UTC 일요일 23:00 에 발화한다.
        assertThat(collectFireKst.getDayOfWeek()).isEqualTo(DayOfWeek.MONDAY);
        assertThat(collectFireKst.toLocalTime().toString()).isEqualTo("08:00");
        assertThat(collectFireUtc.getDayOfWeek()).isEqualTo(DayOfWeek.SUNDAY);
        assertThat(collectFireUtc.toLocalTime().toString()).isEqualTo("23:00");

        // 수집 직후 처음 도는 시점 트리거 = 월요일 09:30 UTC.
        ZonedDateTime nextTriggerFire = triggerCron.next(collectFireUtc);
        assertThat(nextTriggerFire.getDayOfWeek()).isEqualTo(DayOfWeek.MONDAY);
        assertThat(nextTriggerFire.toLocalTime().toString()).isEqualTo("09:30");

        // 핵심 불변식: 수집이 도는 주기에는 CHANGE 배치가 시점 트리거보다 먼저 dispatch 를 선점한다.
        assertThat(collectFireUtc)
                .as("수집(일 23:00 UTC)이 직후 시점 트리거(월 09:30 UTC)보다 선행해야 cross-dedup(CHANGE 우선) 성립")
                .isBefore(nextTriggerFire);
        // 수집 → 임베딩 동기화 → CHANGE 알림 파이프라인이 끝날 마진: 약 10.5시간.
        assertThat(Duration.between(collectFireUtc, nextTriggerFire).toHours())
                .isGreaterThanOrEqualTo(10L);

        // 수집이 없는 요일(화요일 09:30 UTC 트리거)에는 그 이전에 새 수집 발화가 없다 →
        // 선점할 CHANGE 배치가 없으므로 cross-dedup 경합 대상 자체가 생기지 않는다.
        ZonedDateTime tuesdayTriggerFire = triggerCron.next(nextTriggerFire);
        assertThat(tuesdayTriggerFire.getDayOfWeek()).isEqualTo(DayOfWeek.TUESDAY);
        assertThat(collectCron.next(nextTriggerFire.withZoneSameInstant(kst)).withZoneSameInstant(utc))
                .as("월요일 트리거 이후 다음 수집은 그 다음 주 — 화요일 트리거 시점엔 새 CHANGE 배치가 없다")
                .isAfter(tuesdayTriggerFire);
    }
}
