package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.Carryover;
import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatMessageRole;
import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.domain.ChatTurn;
import dev.jazzybyte.onseoul.chat.domain.PrevEntity;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase.PrepareResult;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.ServiceCardParserPort;
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
    /** carryover prev_entities 최대 건수(초과 시 앞쪽 유지). */
    private static final int MAX_PREV_ENTITIES = 10;

    private final SaveChatRoomPort saveChatRoomPort;
    private final LoadChatRoomPort loadChatRoomPort;
    private final SaveChatMessagePort saveChatMessagePort;
    private final LoadChatMessagePort loadChatMessagePort;
    private final ServiceCardParserPort serviceCardParserPort;
    private final ChatHistoryProperties historyProperties;

    @Override
    @Transactional
    public PrepareResult prepare(SendQueryCommand command) {
        boolean created = command.roomId() == null;
        ChatRoom room = resolveRoom(command);
        // 현재 질문을 저장하기 "전"에 직전 N턴과 carryover를 조립한다(현재 질문이 섞이지 않도록).
        List<ChatMessage> recent = loadRecentMessages(room.getId());
        List<ChatTurn> history = toHistory(recent);
        Carryover carryover = buildCarryover(recent);
        Long seq = saveChatMessagePort.nextSeq();
        ChatMessage userMessage = ChatMessage.create(room.getId(), seq, ChatMessageRole.USER, command.question());
        saveChatMessagePort.save(userMessage);
        return new PrepareResult(room.getId(), seq, created, history, carryover);
    }

    /**
     * 직전 N개 메시지(최대 maxTurns*2)를 과거 → 최신 순으로 조회한다.
     * 조회 실패 시 빈 리스트로 폴백한다(스트림은 정상 진행).
     */
    private List<ChatMessage> loadRecentMessages(Long roomId) {
        try {
            return loadChatMessagePort
                    .findRecentByRoomIdOrderBySeqAsc(roomId, historyProperties.maxMessages());
        } catch (Exception e) {
            log.warn("[Chat] history 조회 실패 - 빈 맥락으로 폴백: roomId={}", roomId, e);
            return List.of();
        }
    }

    /** 직전 메시지를 ChatTurn으로 변환한다. content는 메시지당 길이 캡으로 truncate한다. */
    private List<ChatTurn> toHistory(List<ChatMessage> recent) {
        return recent.stream()
                .map(msg -> new ChatTurn(
                        msg.getRole().name().toLowerCase(),
                        truncate(msg.getContent(), historyProperties.maxCharsPerMessage())))
                .toList();
    }

    /**
     * 직전(가장 최신) ASSISTANT 메시지에서 carryover를 조립한다.
     * service_cards(opaque JSON)는 adapter(ServiceCardParserPort)가 [{service_id, label}]로 파싱한다(최대 10건).
     * prev_intent는 그 메시지의 intent(없으면 null). prev_reasoning은 그 메시지 decision의 user_rationale을
     * adapter가 추출한 값(decision 미수신/추출 실패면 null — 하위호환).
     * 직전 ASSISTANT가 없거나 파싱 실패면 빈 carryover로 폴백한다.
     */
    private Carryover buildCarryover(List<ChatMessage> recent) {
        try {
            ChatMessage lastAssistant = null;
            for (ChatMessage msg : recent) { // 과거 → 최신 순이므로 끝까지 훑으면 가장 최신 ASSISTANT가 남는다
                if (msg.getRole() == ChatMessageRole.ASSISTANT) {
                    lastAssistant = msg;
                }
            }
            if (lastAssistant == null) {
                return Carryover.empty();
            }
            List<PrevEntity> prevEntities =
                    serviceCardParserPort.parsePrevEntities(lastAssistant.getServiceCards(), MAX_PREV_ENTITIES);
            String prevReasoning = serviceCardParserPort.parseUserRationale(lastAssistant.getDecision());
            return new Carryover(prevEntities, lastAssistant.getIntent(), prevReasoning);
        } catch (Exception e) {
            log.warn("[Chat] carryover 조립 실패 - 빈 carryover로 폴백", e);
            return Carryover.empty();
        }
    }

    @Override
    @Transactional
    public void saveAnswer(long roomId, String answer, String serviceCardsJson, String intent, String decisionJson) {
        // 멱등 가드: 직전 USER 메시지 이후 ASSISTANT가 이미 있으면(= 마지막 메시지가 ASSISTANT) 저장 생략.
        // 재시도/중복 요청/모든-종료-경로-저장이 겹쳐도 같은 턴에 ASSISTANT가 중복 INSERT되지 않게 한다.
        if (lastMessageIsAssistant(roomId)) {
            log.debug("[Chat] ASSISTANT 응답 이미 존재 - 중복 저장 생략: roomId={}", roomId);
            return;
        }
        Long seq = saveChatMessagePort.nextSeq();
        ChatMessage assistantMessage = ChatMessage.create(
                roomId, seq, ChatMessageRole.ASSISTANT, answer, serviceCardsJson, intent, decisionJson);
        saveChatMessagePort.save(assistantMessage);
    }

    private boolean lastMessageIsAssistant(long roomId) {
        List<ChatMessage> recent = loadChatMessagePort.findRecentByRoomIdOrderBySeqAsc(roomId, 1);
        return !recent.isEmpty()
                && recent.get(recent.size() - 1).getRole() == ChatMessageRole.ASSISTANT;
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
