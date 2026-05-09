package dev.jazzybyte.onseoul.adapter.out.persistence.chat;

import dev.jazzybyte.onseoul.domain.model.ChatMessage;
import dev.jazzybyte.onseoul.domain.model.ChatMessageRole;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

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

    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt;

    @PrePersist
    void prePersist() {
        if (createdAt == null) createdAt = OffsetDateTime.now();
    }

    public ChatMessage toDomain() {
        return new ChatMessage(id, roomId, seq, ChatMessageRole.valueOf(role), content, createdAt);
    }

    public static ChatMessageJpaEntity fromDomain(ChatMessage message) {
        ChatMessageJpaEntity entity = new ChatMessageJpaEntity();
        entity.id = message.getId();
        entity.roomId = message.getRoomId();
        entity.seq = message.getSeq();
        entity.role = message.getRole().name();
        entity.content = message.getContent();
        entity.createdAt = message.getCreatedAt();
        return entity;
    }
}
