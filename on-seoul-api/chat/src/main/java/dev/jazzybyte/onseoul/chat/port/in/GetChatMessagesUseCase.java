package dev.jazzybyte.onseoul.chat.port.in;

import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatRoom;

import java.util.List;

public interface GetChatMessagesUseCase {

    ChatHistory get(Long userId, Long roomId);

    record ChatHistory(ChatRoom room, List<ChatMessage> messages) {}
}
