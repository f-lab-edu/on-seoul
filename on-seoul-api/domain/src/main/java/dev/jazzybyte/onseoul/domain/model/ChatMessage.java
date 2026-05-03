package dev.jazzybyte.onseoul.domain.model;

import lombok.Getter;

import java.time.OffsetDateTime;

@Getter
public class ChatMessage {

    private Long id;
    private Long roomId;
    private Long seq;
    private ChatMessageRole role;
    private String content;
    private OffsetDateTime createdAt;

    public ChatMessage(Long id, Long roomId, Long seq, ChatMessageRole role,
                       String content, OffsetDateTime createdAt) {
        this.id = id;
        this.roomId = roomId;
        this.seq = seq;
        this.role = role;
        this.content = content;
        this.createdAt = createdAt;
    }

    public static ChatMessage create(Long roomId, Long seq, ChatMessageRole role, String content) {
        ChatMessage msg = new ChatMessage();
        msg.roomId = roomId;
        msg.seq = seq;
        msg.role = role;
        msg.content = content;
        msg.createdAt = OffsetDateTime.now();
        return msg;
    }

    private ChatMessage() {}
}
