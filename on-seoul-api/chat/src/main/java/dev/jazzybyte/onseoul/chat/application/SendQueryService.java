package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatMessageRole;
import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.domain.ChatTurn;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase.PrepareResult;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatRoomPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Slf4j
@Service
@RequiredArgsConstructor
public class SendQueryService implements SendQueryUseCase {

    private static final int TITLE_MAX_LENGTH = 50;

    private final SaveChatRoomPort saveChatRoomPort;
    private final LoadChatRoomPort loadChatRoomPort;
    private final SaveChatMessagePort saveChatMessagePort;
    private final LoadChatMessagePort loadChatMessagePort;
    private final ChatHistoryProperties historyProperties;

    @Override
    @Transactional
    public PrepareResult prepare(SendQueryCommand command) {
        ChatRoom room = resolveRoom(command);
        // 현재 질문을 저장하기 "전"에 직전 N턴을 조립한다(현재 질문이 history에 섞이지 않도록).
        List<ChatTurn> history = loadRecentHistory(room.getId());
        Long seq = saveChatMessagePort.nextSeq();
        ChatMessage userMessage = ChatMessage.create(room.getId(), seq, ChatMessageRole.USER, command.question());
        saveChatMessagePort.save(userMessage);
        return new PrepareResult(room.getId(), seq, history);
    }

    /**
     * 직전 N턴(최대 maxTurns*2 메시지)을 과거 → 최신 순으로 조회해 ChatTurn으로 변환한다.
     * content는 메시지당 길이 캡으로 truncate한다. 조회 실패 시 빈 리스트로 폴백한다(스트림은 정상 진행).
     */
    private List<ChatTurn> loadRecentHistory(Long roomId) {
        try {
            return loadChatMessagePort
                    .findRecentByRoomIdOrderBySeqAsc(roomId, historyProperties.maxMessages())
                    .stream()
                    .map(msg -> new ChatTurn(
                            msg.getRole().name().toLowerCase(),
                            truncate(msg.getContent(), historyProperties.maxCharsPerMessage())))
                    .toList();
        } catch (Exception e) {
            log.warn("[Chat] history 조회 실패 - 빈 맥락으로 폴백: roomId={}", roomId, e);
            return List.of();
        }
    }

    @Override
    @Transactional
    public void saveAnswer(long roomId, String answer) {
        Long seq = saveChatMessagePort.nextSeq();
        ChatMessage assistantMessage = ChatMessage.create(roomId, seq, ChatMessageRole.ASSISTANT, answer);
        saveChatMessagePort.save(assistantMessage);
    }

    private ChatRoom resolveRoom(SendQueryCommand command) {
        if (command.roomId() == null) {
            String title = truncate(command.question(), TITLE_MAX_LENGTH);
            ChatRoom savedRoom = saveChatRoomPort.save(ChatRoom.create(command.userId(), title));
            log.info("[Chat] 새 ChatRoom 생성 - roomId={}, userId={}, title={}", savedRoom.getId(), command.userId(), title);
            return savedRoom;
        }
        return loadChatRoomPort.findActiveByIdAndUserId(command.roomId(), command.userId())
                .orElseThrow(() -> new OnSeoulApiException(ErrorCode.CHAT_ROOM_NOT_FOUND));
    }

    /**
     * 코드포인트(글자) 기준으로 절단한다. UTF-16 char 단위 substring과 달리
     * 이모지 등 surrogate pair 경계에서 깨진 문자가 나가지 않도록 보장한다.
     */
    private String truncate(String text, int maxLength) {
        if (text == null) return "";
        if (text.codePointCount(0, text.length()) <= maxLength) return text;
        int endIndex = text.offsetByCodePoints(0, maxLength);
        return text.substring(0, endIndex);
    }
}
