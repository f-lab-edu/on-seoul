package dev.jazzybyte.onseoul.chat.port.out;

import dev.jazzybyte.onseoul.chat.domain.ChatRoom;

public interface SaveChatRoomPort {
    ChatRoom save(ChatRoom room);
}
