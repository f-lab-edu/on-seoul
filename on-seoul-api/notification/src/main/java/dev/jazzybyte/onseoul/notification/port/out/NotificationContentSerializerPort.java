package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationContent;

/**
 * {@link NotificationContent} ↔ JSON 직렬화 아웃바운드 포트.
 *
 * <p>발송 콘텐츠를 {@code notification_dispatches.notification_payload}(JSONB)에 저장할
 * raw JSON으로 직렬화하고, 재시도 시 도메인 객체로 역직렬화한다.
 * 도메인은 JSON을 모르므로 직렬화 책임을 어댑터 계층으로 분리한다
 * ({@link SubscriptionFilterParserPort}와 동일 패턴).
 */
public interface NotificationContentSerializerPort {

    /** 직렬화 실패 시 null 반환 (페이로드 저장 실패가 발송을 막지 않는다). */
    String serialize(NotificationContent content);

    /** null/blank/파싱 실패 시 null 반환 (호출자는 평문 폴백으로 분기). */
    NotificationContent deserialize(String json);
}
