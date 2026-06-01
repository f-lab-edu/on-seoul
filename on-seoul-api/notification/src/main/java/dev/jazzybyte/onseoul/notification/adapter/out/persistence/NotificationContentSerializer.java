package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.port.out.NotificationContentSerializerPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

/**
 * {@link NotificationContent} ↔ JSON 직렬화/역직렬화 매퍼.
 *
 * <p>{@code notification_dispatches.notification_payload}(JSONB) 컬럼에 저장할 raw JSON을
 * 만들고, 재시도 시 다시 도메인 객체로 복원한다. 도메인은 JSON을 모르므로 직렬화 책임은
 * 이 매퍼 계층에 둔다.
 */
@Slf4j
@Component
public class NotificationContentSerializer implements NotificationContentSerializerPort {

    private final ObjectMapper objectMapper;

    public NotificationContentSerializer(final ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    /**
     * 발송 콘텐츠를 JSON 문자열로 직렬화한다. 실패 시 null을 반환한다 — 페이로드 저장 실패가
     * 발송 자체를 막아서는 안 되며, payload null이면 재시도는 평문 폴백 경로를 사용한다.
     */
    @Override
    public String serialize(NotificationContent content) {
        if (content == null) {
            return null;
        }
        try {
            return objectMapper.writeValueAsString(content);
        } catch (Exception e) {
            log.warn("[NotificationContentSerializer] 직렬화 실패 — payload null로 진행: {}", e.getMessage());
            return null;
        }
    }

    /**
     * JSON 문자열을 발송 콘텐츠로 역직렬화한다. null/blank/파싱 실패 시 null을 반환한다 —
     * 호출자는 null이면 평문 폴백 경로로 분기한다.
     */
    @Override
    public NotificationContent deserialize(String json) {
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            return objectMapper.readValue(json, NotificationContent.class);
        } catch (Exception e) {
            log.warn("[NotificationContentSerializer] 역직렬화 실패 — 평문 폴백 사용: {}", e.getMessage());
            return null;
        }
    }
}
