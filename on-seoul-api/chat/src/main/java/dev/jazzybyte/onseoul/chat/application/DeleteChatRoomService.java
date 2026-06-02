package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.port.in.DeleteChatRoomUseCase;
import dev.jazzybyte.onseoul.chat.port.out.DeleteChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
@Transactional
public class DeleteChatRoomService implements DeleteChatRoomUseCase {

    private final LoadChatRoomPort loadChatRoomPort;
    private final DeleteChatRoomPort deleteChatRoomPort;

    @Override
    public void delete(Long userId, Long roomId) {
        ChatRoom room = loadChatRoomPort.findActiveByIdAndUserId(roomId, userId)
                .orElseThrow(() -> new OnSeoulApiException(ErrorCode.CHAT_ROOM_NOT_FOUND));

        room.softDelete();
        deleteChatRoomPort.softDelete(room);
    }
}
