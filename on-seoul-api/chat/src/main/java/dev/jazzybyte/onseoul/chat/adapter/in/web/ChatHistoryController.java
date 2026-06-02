package dev.jazzybyte.onseoul.chat.adapter.in.web;

import dev.jazzybyte.onseoul.chat.port.in.DeleteChatRoomUseCase;
import dev.jazzybyte.onseoul.chat.port.in.GetChatMessagesUseCase;
import dev.jazzybyte.onseoul.chat.port.in.ListChatRoomsUseCase;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/api/chat")
@RequiredArgsConstructor
public class ChatHistoryController {

    private final ListChatRoomsUseCase listChatRoomsUseCase;
    private final GetChatMessagesUseCase getChatMessagesUseCase;
    private final DeleteChatRoomUseCase deleteChatRoomUseCase;

    @GetMapping("/rooms")
    public ResponseEntity<RoomListResponse> listRooms(
            @RequestAttribute(required = false) Long userId,
            @RequestParam(required = false) String cursor,
            @RequestParam(required = false, defaultValue = "20") int size) {
        if (userId == null) {
            throw new OnSeoulApiException(ErrorCode.UNAUTHORIZED);
        }
        ListChatRoomsUseCase.RoomPage page = listChatRoomsUseCase.list(userId, cursor, size);
        List<RoomSummaryResponse> rooms = page.rooms().stream()
                .map(RoomSummaryResponse::from)
                .toList();
        return ResponseEntity.ok(new RoomListResponse(rooms, page.nextCursor()));
    }

    @GetMapping("/rooms/{roomId}/messages")
    public ResponseEntity<ChatHistoryResponse> getMessages(
            @RequestAttribute(required = false) Long userId,
            @PathVariable Long roomId) {
        if (userId == null) {
            throw new OnSeoulApiException(ErrorCode.UNAUTHORIZED);
        }
        GetChatMessagesUseCase.ChatHistory history = getChatMessagesUseCase.get(userId, roomId);
        return ResponseEntity.ok(ChatHistoryResponse.from(history));
    }

    @DeleteMapping("/rooms/{roomId}")
    public ResponseEntity<Void> deleteRoom(
            @RequestAttribute(required = false) Long userId,
            @PathVariable Long roomId) {
        if (userId == null) {
            throw new OnSeoulApiException(ErrorCode.UNAUTHORIZED);
        }
        deleteChatRoomUseCase.delete(userId, roomId);
        return ResponseEntity.noContent().build();
    }
}
