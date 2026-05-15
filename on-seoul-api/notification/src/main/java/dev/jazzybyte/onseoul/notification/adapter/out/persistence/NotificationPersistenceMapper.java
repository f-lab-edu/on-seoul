package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;
import org.springframework.stereotype.Component;

import java.util.Set;

@Component
class NotificationPersistenceMapper {

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

    NotificationDispatch toDomain(NotificationDispatchJpaEntity e) {
        return new NotificationDispatch(
                e.getId(),
                e.getSubscriptionId(),
                e.getChangeLogId(),
                e.getStatus(),
                e.getAttemptCount(),
                e.getSentAt(),
                e.getGeneratedTitle(),
                e.getGeneratedBody(),
                e.getTemplateSource(),
                e.getLastError(),
                e.getCreatedAt(),
                e.getUpdatedAt());
    }
}
