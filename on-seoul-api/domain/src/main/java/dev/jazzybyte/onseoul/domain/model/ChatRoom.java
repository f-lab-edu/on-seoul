package dev.jazzybyte.onseoul.domain.model;

import lombok.Getter;

import java.time.OffsetDateTime;

@Getter
public class ChatRoom {

    private Long id;
    private Long userId;
    private String title;
    private boolean titleGenerated;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;

    public ChatRoom(Long id, Long userId, String title, boolean titleGenerated,
                    OffsetDateTime createdAt, OffsetDateTime updatedAt) {
        this.id = id;
        this.userId = userId;
        this.title = title;
        this.titleGenerated = titleGenerated;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    public static ChatRoom create(Long userId, String title) {
        ChatRoom room = new ChatRoom();
        room.userId = userId;
        room.title = title;
        room.titleGenerated = false;
        room.createdAt = OffsetDateTime.now();
        room.updatedAt = OffsetDateTime.now();
        return room;
    }

    private ChatRoom() {}

    public void updateTitle(String title) {
        this.title = title;
        this.titleGenerated = true;
        this.updatedAt = OffsetDateTime.now();
    }
}
