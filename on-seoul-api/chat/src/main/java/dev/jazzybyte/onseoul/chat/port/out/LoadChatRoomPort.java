package dev.jazzybyte.onseoul.chat.port.out;

import dev.jazzybyte.onseoul.chat.domain.ChatRoom;

import java.util.List;
import java.util.Optional;

public interface LoadChatRoomPort {

    Optional<ChatRoom> findById(Long id);

    Optional<ChatRoom> findActiveByIdAndUserId(Long id, Long userId);

    List<ChatRoom> findActiveByUserId(Long userId, RoomCursor cursor, int limit);
}
