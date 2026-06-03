package dev.jazzybyte.onseoul.notification.domain;

/**
 * 시점 트리거(OPEN_DAY/BEFORE_RECEIPT_D1/DEADLINE_DDAY) 조회 결과 값 객체.
 *
 * <p>{@code public_service_reservations} 1행 = 시점 알림 1건(service_id 단위)에 대응한다.
 * change_log 기반 {@link ServiceChange} 와 달리 "변경"이 없으므로 변경 라인 필드가 없다.
 * 메타(serviceName 이하)는 AI 템플릿/카드 조립에 그대로 전달한다(모두 null 허용).
 * 날짜는 변환 없이 ISO 문자열로 그대로 전달한다.
 */
public record ScheduledServiceMatch(
        String serviceId,
        String serviceName,
        String serviceUrl,
        String imageUrl,
        String placeName,
        String areaName,
        String serviceStatus,
        String targetInfo,
        String receiptStartDt,
        String receiptEndDt
) {}
