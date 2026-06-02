package dev.jazzybyte.onseoul.chat.adapter.in.web;

import dev.jazzybyte.onseoul.chat.domain.ChatMessage;

import java.time.OffsetDateTime;

public record MessageResponse(
        Long seq,
        String role,
        String content,
        OffsetDateTime createdAt
) {
    public static MessageResponse from(ChatMessage message) {
        return new MessageResponse(
                message.getSeq(),
                message.getRole().name(),
                message.getContent(),
                message.getCreatedAt()
        );
    }
}
