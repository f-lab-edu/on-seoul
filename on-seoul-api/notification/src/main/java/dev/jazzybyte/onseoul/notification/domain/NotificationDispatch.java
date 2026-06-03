package dev.jazzybyte.onseoul.notification.domain;

import lombok.Getter;

import java.time.Instant;
import java.time.LocalDate;

/**
 * 배치 × 구독 단위 알림 발송 이력 (ADR-0004).
 *
 * <p>per-change 모델에서 제거된 필드: {@code changeLogId}.
 * 추가된 필드: {@code batchId}, {@code attemptCount}.
 */
@Getter
public class NotificationDispatch {

    public static final int MAX_ATTEMPTS = 5;

    private Long id;
    private Long batchId;
    private Long subscriptionId;
    /**
     * 발송 트리거 종류. 기존 CHANGE 경로는 {@link TriggerType#CHANGE} 로 유지된다(기본값).
     * 시점 트리거(OPEN_DAY/BEFORE_RECEIPT_D1/DEADLINE_DDAY)는 service_id 단위로 발행된다.
     */
    private TriggerType triggerType = TriggerType.CHANGE;
    /**
     * 시점 트리거 dispatch 의 dedup 기준일(발송 대상 달력 날짜). CHANGE 는 null.
     * DATE 타입 — 타임존 변환 없이 "날짜" 단위로 다룬다.
     */
    private LocalDate dispatchDate;
    /**
     * 시점 트리거 대상 서비스 ID. CHANGE 는 여러 service 를 payload 에 묶으므로 null.
     */
    private String serviceId;
    private DispatchStatus status;
    private Instant sentAt;
    private String generatedTitle;
    private String generatedBody;
    private TemplateSource templateSource;
    private String lastError;
    private int attemptCount;
    /**
     * 발송 콘텐츠({@code NotificationContent})의 직렬화 JSON 문자열 (재시도 무손실 복원용).
     * 직렬화/역직렬화는 어댑터/매퍼 계층 책임 — 도메인은 raw String만 보관한다.
     * 09 마이그레이션 이전 row 또는 조립 전이면 null.
     */
    private String notificationPayload;
    private Instant createdAt;
    private Instant updatedAt;

    /** Reconstitute from persistence. */
    public NotificationDispatch(Long id, Long batchId, Long subscriptionId,
                                TriggerType triggerType, LocalDate dispatchDate, String serviceId,
                                DispatchStatus status,
                                Instant sentAt, String generatedTitle, String generatedBody,
                                TemplateSource templateSource, String lastError,
                                int attemptCount, String notificationPayload,
                                Instant createdAt, Instant updatedAt) {
        this.id = id;
        this.batchId = batchId;
        this.subscriptionId = subscriptionId;
        this.triggerType = triggerType == null ? TriggerType.CHANGE : triggerType;
        this.dispatchDate = dispatchDate;
        this.serviceId = serviceId;
        this.status = status;
        this.sentAt = sentAt;
        this.generatedTitle = generatedTitle;
        this.generatedBody = generatedBody;
        this.templateSource = templateSource;
        this.lastError = lastError;
        this.attemptCount = attemptCount;
        this.notificationPayload = notificationPayload;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    private NotificationDispatch() {}

    /**
     * Factory: 신규 PENDING CHANGE dispatch 생성(기존 동작).
     * triggerType=CHANGE, serviceId=null, dispatchDate=null.
     */
    public static NotificationDispatch create(Long batchId, Long subscriptionId) {
        NotificationDispatch d = new NotificationDispatch();
        d.batchId = batchId;
        d.subscriptionId = subscriptionId;
        d.triggerType = TriggerType.CHANGE;
        d.status = DispatchStatus.PENDING;
        d.attemptCount = 0;
        Instant now = Instant.now();
        d.createdAt = now;
        d.updatedAt = now;
        return d;
    }

    /**
     * Factory: 신규 PENDING 시점 트리거 dispatch 생성.
     * service_id 단위로 발행되며 (subscription_id, service_id, dispatch_date) 로 dedup 된다.
     *
     * @param triggerType  시점 트리거(OPEN_DAY/BEFORE_RECEIPT_D1/DEADLINE_DDAY) — CHANGE 전달 금지
     * @param serviceId    대상 서비스 ID (필수)
     * @param dispatchDate 발송 대상 달력 날짜 (dedup 기준, 필수)
     */
    public static NotificationDispatch createScheduled(Long batchId, Long subscriptionId,
                                                       TriggerType triggerType,
                                                       String serviceId, LocalDate dispatchDate) {
        if (triggerType == null || triggerType.isChange()) {
            throw new IllegalArgumentException(
                    "createScheduled 는 시점 트리거만 허용한다: " + triggerType);
        }
        if (serviceId == null || serviceId.isBlank()) {
            throw new IllegalArgumentException("시점 트리거 dispatch 는 serviceId 가 필수다");
        }
        if (dispatchDate == null) {
            throw new IllegalArgumentException("시점 트리거 dispatch 는 dispatchDate 가 필수다");
        }
        NotificationDispatch d = new NotificationDispatch();
        d.batchId = batchId;
        d.subscriptionId = subscriptionId;
        d.triggerType = triggerType;
        d.serviceId = serviceId;
        d.dispatchDate = dispatchDate;
        d.status = DispatchStatus.PENDING;
        d.attemptCount = 0;
        Instant now = Instant.now();
        d.createdAt = now;
        d.updatedAt = now;
        return d;
    }

    /**
     * 발송 콘텐츠 직렬화 JSON을 할당한다(발송 직전 호출).
     * 직렬화는 어댑터/매퍼 계층에서 수행하고, 결과 raw String만 도메인에 전달한다.
     */
    public void assignPayload(String notificationPayload) {
        this.notificationPayload = notificationPayload;
    }

    /** 성공 기록. */
    public void markSuccess(String title, String body, TemplateSource source) {
        this.status = DispatchStatus.SUCCESS;
        Instant now = Instant.now();
        this.sentAt = now;
        this.generatedTitle = title;
        this.generatedBody = body;
        this.templateSource = source;
        this.lastError = null;
        this.updatedAt = now;
    }

    /**
     * 실패 기록. title/body/source를 함께 저장하여 재시도 스케줄러가 재사용할 수 있게 한다.
     * last_notified_at은 갱신되지 않으므로 다음 배치가 자동 재시도한다.
     * DEAD 상태로 전환하지 않는다 — {@link #markDead(String)} 를 사용하라.
     */
    public void markFailed(String error, String title, String body, TemplateSource source) {
        this.status = DispatchStatus.FAILED;
        this.lastError = error;
        this.generatedTitle = title;
        this.generatedBody = body;
        this.templateSource = source;
        this.updatedAt = Instant.now();
    }

    /**
     * 재시도 한도 초과 시 DEAD로 전환한다.
     * last_notified_at은 갱신하지 않는다.
     */
    public void markDead(String error) {
        this.status = DispatchStatus.DEAD;
        this.lastError = error;
        this.updatedAt = Instant.now();
    }

    /** attempt_count를 1 증가시킨다. */
    public void incrementAttemptCount() {
        this.attemptCount++;
    }

    /**
     * attempt_count가 최대 재시도 횟수(5)에 도달했으면 true.
     * {@link #incrementAttemptCount()} 호출 후 이 메서드로 DEAD 전환 여부를 판단한다.
     */
    public boolean isRetryExhausted() {
        return this.attemptCount >= MAX_ATTEMPTS;
    }

    public boolean isPending() {
        return this.status == DispatchStatus.PENDING;
    }
}
