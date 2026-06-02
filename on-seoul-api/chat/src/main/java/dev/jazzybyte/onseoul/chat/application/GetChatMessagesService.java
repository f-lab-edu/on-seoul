package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.port.in.GetChatMessagesUseCase;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class GetChatMessagesService implements GetChatMessagesUseCase {

    private final LoadChatRoomPort loadChatRoomPort;
    private final LoadChatMessagePort loadChatMessagePort;

    @Override
    public ChatHistory get(Long userId, Long roomId) {
        ChatRoom room = loadChatRoomPort.findActiveByIdAndUserId(roomId, userId)
                .orElseThrow(() -> new OnSeoulApiException(ErrorCode.CHAT_ROOM_NOT_FOUND));

        List<ChatMessage> messages = loadChatMessagePort.findByRoomIdOrderBySeqAsc(roomId);

        return new ChatHistory(room, messages);
    }
}
