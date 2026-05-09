package dev.jazzybyte.onseoul.domain.port.out;

import dev.jazzybyte.onseoul.domain.model.ChatRoom;

import java.util.Optional;

public interface LoadChatRoomPort {
    Optional<ChatRoom> findById(Long id);
}
