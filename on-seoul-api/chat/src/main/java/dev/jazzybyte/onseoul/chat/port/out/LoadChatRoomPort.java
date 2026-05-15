package dev.jazzybyte.onseoul.chat.port.out;

import dev.jazzybyte.onseoul.chat.domain.ChatRoom;

import java.util.Optional;

public interface LoadChatRoomPort {
    Optional<ChatRoom> findById(Long id);
}
