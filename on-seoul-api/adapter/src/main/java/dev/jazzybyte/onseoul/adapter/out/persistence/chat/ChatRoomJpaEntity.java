package dev.jazzybyte.onseoul.adapter.out.persistence.chat;

import dev.jazzybyte.onseoul.domain.model.ChatRoom;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.OffsetDateTime;

@Entity
@Table(name = "chat_rooms")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class ChatRoomJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "user_id", nullable = false)
    private Long userId;

    @Column(nullable = false, length = 500)
    private String title;

    @Column(name = "is_title_generated", nullable = false)
    private boolean titleGenerated;

    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private OffsetDateTime updatedAt;

    @PrePersist
    void prePersist() {
        OffsetDateTime now = OffsetDateTime.now();
        if (createdAt == null) createdAt = now;
        if (updatedAt == null) updatedAt = now;
    }

    @PreUpdate
    void preUpdate() {
        updatedAt = OffsetDateTime.now();
    }

    public ChatRoom toDomain() {
        return new ChatRoom(id, userId, title, titleGenerated, createdAt, updatedAt);
    }

    public static ChatRoomJpaEntity fromDomain(ChatRoom room) {
        ChatRoomJpaEntity entity = new ChatRoomJpaEntity();
        entity.id = room.getId();
        entity.userId = room.getUserId();
        entity.title = room.getTitle();
        entity.titleGenerated = room.isTitleGenerated();
        entity.createdAt = room.getCreatedAt();
        entity.updatedAt = room.getUpdatedAt();
        return entity;
    }
}
