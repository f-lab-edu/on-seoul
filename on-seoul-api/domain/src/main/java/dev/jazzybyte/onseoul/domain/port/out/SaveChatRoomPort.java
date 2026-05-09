package dev.jazzybyte.onseoul.domain.port.out;

import dev.jazzybyte.onseoul.domain.model.ChatRoom;

public interface SaveChatRoomPort {
    ChatRoom save(ChatRoom room);
}
