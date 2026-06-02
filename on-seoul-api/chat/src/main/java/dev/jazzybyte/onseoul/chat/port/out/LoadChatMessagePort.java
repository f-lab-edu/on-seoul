package dev.jazzybyte.onseoul.chat.port.out;

import dev.jazzybyte.onseoul.chat.domain.ChatMessage;

import java.util.List;

public interface LoadChatMessagePort {
    List<ChatMessage> findByRoomIdOrderBySeqAsc(Long roomId);
}
