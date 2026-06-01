package dev.jazzybyte.onseoul.notification.domain;

import java.time.Instant;

/**
 * notification BC 전용 값 객체. collection BC의 ServiceChangeLog를 직접 import하지 않기 위해 정의한다.
 *
 * <p>service_change_log JOIN public_service_reservations 결과를 담는다.
 * serviceName 이하 필드는 매칭된 예약(public_service_reservations)의 컨텍스트로,
 * AI 템플릿 생성 시 풍부한 메시지를 만들기 위해 그대로 전달한다(모두 null 허용).
 * 날짜(receiptStartDt/EndDt)도 변환 없이 문자열로 그대로 전달한다.
 */
public record ServiceChange(
        Long id,
        String serviceId,
        String changeType,
        String fieldName,
        String oldValue,
        String newValue,
        Instant changedAt,
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
