package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.model.ChatMessage;
import dev.jazzybyte.onseoul.domain.model.ChatMessageRole;
import dev.jazzybyte.onseoul.domain.model.ChatRoom;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryUseCase;
import dev.jazzybyte.onseoul.domain.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.domain.port.out.SaveChatMessagePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveChatRoomPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class SendQueryService implements SendQueryUseCase {

    private static final int TITLE_MAX_LENGTH = 50;

    private final SaveChatRoomPort saveChatRoomPort;
    private final LoadChatRoomPort loadChatRoomPort;
    private final SaveChatMessagePort saveChatMessagePort;

    public SendQueryService(final SaveChatRoomPort saveChatRoomPort,
                            final LoadChatRoomPort loadChatRoomPort,
                            final SaveChatMessagePort saveChatMessagePort) {
        this.saveChatRoomPort = saveChatRoomPort;
        this.loadChatRoomPort = loadChatRoomPort;
        this.saveChatMessagePort = saveChatMessagePort;
    }

    @Override
    @Transactional
    public Long prepare(SendQueryCommand command) {
        ChatRoom room = resolveRoom(command);
        Long seq = saveChatMessagePort.nextSeq();
        ChatMessage userMessage = ChatMessage.create(room.getId(), seq, ChatMessageRole.USER, command.question());
        saveChatMessagePort.save(userMessage);
        return room.getId();
    }

    @Override
    @Transactional
    public void saveAnswer(Long roomId, String answer) {
        Long seq = saveChatMessagePort.nextSeq();
        ChatMessage assistantMessage = ChatMessage.create(roomId, seq, ChatMessageRole.ASSISTANT, answer);
        saveChatMessagePort.save(assistantMessage);
    }

    private ChatRoom resolveRoom(SendQueryCommand command) {
        if (command.roomId() == null) {
            String title = truncate(command.question(), TITLE_MAX_LENGTH);
            ChatRoom newRoom = ChatRoom.create(command.userId(), title);
            return saveChatRoomPort.save(newRoom);
        }
        return loadChatRoomPort.findById(command.roomId())
                .orElseThrow(() -> new OnSeoulApiException(ErrorCode.CHAT_ROOM_NOT_FOUND));
    }

    private String truncate(String text, int maxLength) {
        if (text == null) return "";
        return text.length() <= maxLength ? text : text.substring(0, maxLength);
    }
}
