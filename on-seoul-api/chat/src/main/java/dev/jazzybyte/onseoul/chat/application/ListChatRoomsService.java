package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.port.in.ListChatRoomsUseCase;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.RoomCursor;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class ListChatRoomsService implements ListChatRoomsUseCase {

    private static final int MIN_SIZE = 1;
    private static final int MAX_SIZE = 100;

    private final LoadChatRoomPort loadChatRoomPort;

    @Override
    public RoomPage list(Long userId, String cursor, int size) {
        int clampedSize = Math.max(MIN_SIZE, Math.min(MAX_SIZE, size));

        RoomCursor decodedCursor = null;
        if (cursor != null && !cursor.isBlank()) {
            decodedCursor = ChatRoomCursor.decode(cursor);
        }

        List<ChatRoom> fetched = loadChatRoomPort.findActiveByUserId(userId, decodedCursor, clampedSize + 1);

        if (fetched.size() > clampedSize) {
            ChatRoom last = fetched.get(clampedSize - 1);
            List<ChatRoom> rooms = new ArrayList<>(fetched.subList(0, clampedSize));
            String nextCursor = ChatRoomCursor.encode(last.getUpdatedAt(), last.getId());
            return new RoomPage(rooms, nextCursor);
        }

        return new RoomPage(fetched, null);
    }
}
