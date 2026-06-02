package dev.jazzybyte.onseoul.chat.port.in;

import dev.jazzybyte.onseoul.chat.domain.ChatRoom;

import java.util.List;

public interface ListChatRoomsUseCase {

    RoomPage list(Long userId, String cursor, int size);

    record RoomPage(List<ChatRoom> rooms, String nextCursor) {}
}
