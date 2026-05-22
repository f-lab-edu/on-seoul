package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.out.SubscriptionFilterParserPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.LinkedHashSet;
import java.util.Set;

@Slf4j
@Component
class NotificationPersistenceMapper implements SubscriptionFilterParserPort {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final TypeReference<Set<NotificationChannel>> CHANNEL_SET_TYPE =
            new TypeReference<>() {};

    NotificationSubscription toDomain(NotificationSubscriptionJpaEntity e) {
        return new NotificationSubscription(
                e.getId(),
                e.getUserId(),
                e.getServiceId(),
                e.getFilter(),
                deserializeChannels(e.getChannels()),
                e.getLastNotifiedAt(),
                e.getCreatedAt());
    }

    String serializeChannels(Set<NotificationChannel> channels) {
        try {
            return OBJECT_MAPPER.writeValueAsString(channels);
        } catch (JsonProcessingException ex) {
            throw new IllegalStateException("channels 직렬화 실패", ex);
        }
    }

    private Set<NotificationChannel> deserializeChannels(String json) {
        if (json == null || json.isBlank()) {
            throw new IllegalStateException("channels 컬럼이 null/blank — DB 불변 조건 위반");
        }
        try {
            return OBJECT_MAPPER.readValue(json, CHANNEL_SET_TYPE);
        } catch (JsonProcessingException ex) {
            throw new IllegalStateException("channels 역직렬화 실패: " + json, ex);
        }
    }

    /**
     * NotificationSubscription.filter (JSONB 문자열) → 구조화된 {@link SubscriptionFilter} 변환.
     *
     * <p>인식 키:
     * <ul>
     *   <li>{@code statuses}        — string[] : service_status 화이트리스트</li>
     *   <li>{@code areaNames}       — string[] : area_name 화이트리스트</li>
     *   <li>{@code maxClassNames}   — string[] : max_class_name 화이트리스트 (카테고리)</li>
     * </ul>
     * 빈 객체 {@code {}}나 null이면 {@link SubscriptionFilter#empty()} 반환.
     * 파싱 실패 시 empty()로 fallback (안전한 기본값).
     */
    @Override
    public SubscriptionFilter parse(String filterJson) {
        if (filterJson == null || filterJson.isBlank()) {
            return SubscriptionFilter.empty();
        }
        try {
            JsonNode root = OBJECT_MAPPER.readTree(filterJson);
            if (!root.isObject() || root.isEmpty()) {
                return SubscriptionFilter.empty();
            }
            return new SubscriptionFilter(
                    readStringSet(root, "statuses"),
                    readStringSet(root, "areaNames"),
                    readStringSet(root, "maxClassNames"));
        } catch (JsonProcessingException ex) {
            log.warn("[NotificationPersistenceMapper] filter JSON 파싱 실패 — empty filter로 폴백 (잘못된 filter 값은 ALL 변경에 알림 발송됨): json={}", filterJson, ex);
            return SubscriptionFilter.empty();
        }
    }

    private Set<String> readStringSet(JsonNode root, String key) {
        JsonNode node = root.get(key);
        if (node == null || !node.isArray() || node.isEmpty()) {
            return Set.of();
        }
        Set<String> out = new LinkedHashSet<>();
        node.forEach(n -> {
            if (n.isTextual()) {
                String v = n.asText();
                if (v != null && !v.isBlank()) out.add(v);
            }
        });
        return out;
    }

    NotificationDispatch toDomain(NotificationDispatchJpaEntity e) {
        return new NotificationDispatch(
                e.getId(),
                e.getBatchId(),
                e.getSubscriptionId(),
                e.getStatus(),
                e.getSentAt(),
                e.getGeneratedTitle(),
                e.getGeneratedBody(),
                e.getTemplateSource(),
                e.getLastError(),
                e.getCreatedAt(),
                e.getUpdatedAt());
    }
}
