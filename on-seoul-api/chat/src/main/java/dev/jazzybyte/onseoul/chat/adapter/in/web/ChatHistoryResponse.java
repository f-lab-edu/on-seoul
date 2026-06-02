package dev.jazzybyte.onseoul.chat.adapter.in.web;

import dev.jazzybyte.onseoul.chat.port.in.GetChatMessagesUseCase;

import java.util.List;

public record ChatHistoryResponse(
        Long roomId,
        String title,
        List<MessageResponse> messages
) {
    public static ChatHistoryResponse from(GetChatMessagesUseCase.ChatHistory history) {
        List<MessageResponse> msgs = history.messages().stream()
                .map(MessageResponse::from)
                .toList();
        return new ChatHistoryResponse(
                history.room().getId(),
                history.room().getTitle(),
                msgs
        );
    }
}
