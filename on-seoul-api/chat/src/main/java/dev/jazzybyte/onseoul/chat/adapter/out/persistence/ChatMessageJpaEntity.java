package dev.jazzybyte.onseoul.chat.adapter.out.persistence;

import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatMessageRole;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;

@Entity
@Table(name = "chat_messages")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class ChatMessageJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "room_id", nullable = false)
    private Long roomId;

    @Column(nullable = false)
    private Long seq;

    @Column(nullable = false, length = 20)
    private String role;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String content;

    // raw JSON text passthrough. ASSISTANT 메시지의 service_cards 배열, USER는 null.
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "service_cards")
    private String serviceCards;

    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt;

    @PrePersist
    void prePersist() {
        if (createdAt == null) createdAt = OffsetDateTime.now();
    }

    public ChatMessage toDomain() {
        return new ChatMessage(id, roomId, seq, ChatMessageRole.valueOf(role), content, serviceCards, createdAt);
    }

    public static ChatMessageJpaEntity fromDomain(ChatMessage message) {
        ChatMessageJpaEntity entity = new ChatMessageJpaEntity();
        entity.id = message.getId();
        entity.roomId = message.getRoomId();
        entity.seq = message.getSeq();
        entity.role = message.getRole().name();
        entity.content = message.getContent();
        entity.serviceCards = message.getServiceCards();
        entity.createdAt = message.getCreatedAt();
        return entity;
    }
}
