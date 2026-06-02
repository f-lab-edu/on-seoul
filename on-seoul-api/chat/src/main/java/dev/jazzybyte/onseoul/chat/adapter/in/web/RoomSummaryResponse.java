package dev.jazzybyte.onseoul.chat.adapter.in.web;

import dev.jazzybyte.onseoul.chat.domain.ChatRoom;

import java.time.OffsetDateTime;

public record RoomSummaryResponse(
        Long roomId,
        String title,
        boolean titleGenerated,
        OffsetDateTime createdAt,
        OffsetDateTime updatedAt
) {
    public static RoomSummaryResponse from(ChatRoom room) {
        return new RoomSummaryResponse(
                room.getId(),
                room.getTitle(),
                room.isTitleGenerated(),
                room.getCreatedAt(),
                room.getUpdatedAt()
        );
    }
}
