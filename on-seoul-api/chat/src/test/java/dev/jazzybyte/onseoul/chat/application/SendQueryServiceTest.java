package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatMessageRole;
import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.domain.ChatTurn;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase.PrepareResult;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatRoomPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InOrder;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class SendQueryServiceTest {

    @Mock private SaveChatRoomPort saveChatRoomPort;
    @Mock private LoadChatRoomPort loadChatRoomPort;
    @Mock private SaveChatMessagePort saveChatMessagePort;
    @Mock private LoadChatMessagePort loadChatMessagePort;

    private SendQueryService service;

    @BeforeEach
    void setUp() {
        ChatHistoryProperties historyProperties = new ChatHistoryProperties(5, 1000);
        service = new SendQueryService(saveChatRoomPort, loadChatRoomPort, saveChatMessagePort,
                loadChatMessagePort, historyProperties);
    }

    private ChatRoom savedRoom(Long id) {
        return new ChatRoom(id, 1L, "질문 제목", false,
                OffsetDateTime.now(), OffsetDateTime.now(), null);
    }

    private ChatMessage msg(long roomId, long seq, ChatMessageRole role, String content) {
        return new ChatMessage(seq, roomId, seq, role, content, OffsetDateTime.now());
    }

    @Test
    @DisplayName("prepare() - roomId가 null이면 새 ChatRoom을 생성하고 USER 메시지를 저장한 뒤 PrepareResult를 반환한다")
    void prepare_newRoom_createsRoomAndSavesUserMessage() {
        Long userId = 1L;
        String question = "서울 문화행사 알려줘";
        SendQueryCommand command = new SendQueryCommand(userId, null, question, null, null);

        ChatRoom createdRoom = savedRoom(10L);
        when(saveChatRoomPort.save(any(ChatRoom.class))).thenReturn(createdRoom);
        when(saveChatMessagePort.nextSeq()).thenReturn(1L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        PrepareResult result = service.prepare(command);

        assertThat(result.roomId()).isEqualTo(10L);
        assertThat(result.messageId()).isEqualTo(1L);
        assertThat(result.created()).isTrue();

        ArgumentCaptor<ChatRoom> roomCaptor = ArgumentCaptor.forClass(ChatRoom.class);
        verify(saveChatRoomPort).save(roomCaptor.capture());
        assertThat(roomCaptor.getValue().getUserId()).isEqualTo(userId);
        assertThat(roomCaptor.getValue().getTitle()).isEqualTo(question);

        ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
        verify(saveChatMessagePort).save(msgCaptor.capture());
        assertThat(msgCaptor.getValue().getRole()).isEqualTo(ChatMessageRole.USER);
        assertThat(msgCaptor.getValue().getContent()).isEqualTo(question);
        assertThat(msgCaptor.getValue().getRoomId()).isEqualTo(10L);
    }

    @Test
    @DisplayName("prepare() - question이 50자 초과이면 title을 50자로 잘라 저장한다")
    void prepare_longQuestion_titleTruncatedTo50Chars() {
        String longQuestion = "가".repeat(60);
        SendQueryCommand command = new SendQueryCommand(1L, null, longQuestion, null, null);

        ChatRoom createdRoom = savedRoom(11L);
        when(saveChatRoomPort.save(any(ChatRoom.class))).thenReturn(createdRoom);
        when(saveChatMessagePort.nextSeq()).thenReturn(1L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        service.prepare(command);

        ArgumentCaptor<ChatRoom> captor = ArgumentCaptor.forClass(ChatRoom.class);
        verify(saveChatRoomPort).save(captor.capture());
        assertThat(captor.getValue().getTitle()).hasSize(50);
    }

    @Test
    @DisplayName("prepare() - roomId가 주어지면 기존 방을 재사용하고 USER 메시지를 저장한다")
    void prepare_existingRoom_reusesRoomAndSavesUserMessage() {
        Long userId = 1L;
        Long existingRoomId = 5L;
        String question = "추가 질문";
        SendQueryCommand command = new SendQueryCommand(userId, existingRoomId, question, null, null);

        ChatRoom existingRoom = savedRoom(existingRoomId);
        when(loadChatRoomPort.findActiveByIdAndUserId(existingRoomId, userId)).thenReturn(Optional.of(existingRoom));
        when(saveChatMessagePort.nextSeq()).thenReturn(2L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        PrepareResult result = service.prepare(command);

        assertThat(result.roomId()).isEqualTo(existingRoomId);
        assertThat(result.messageId()).isEqualTo(2L);
        assertThat(result.created()).isFalse();
        verify(saveChatRoomPort, never()).save(any());

        ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
        verify(saveChatMessagePort).save(msgCaptor.capture());
        assertThat(msgCaptor.getValue().getRole()).isEqualTo(ChatMessageRole.USER);
        assertThat(msgCaptor.getValue().getRoomId()).isEqualTo(existingRoomId);
    }

    @Test
    @DisplayName("prepare() - roomId가 주어졌지만 존재하지 않으면 CHAT_ROOM_NOT_FOUND 예외를 던진다")
    void prepare_roomNotFound_throwsException() {
        SendQueryCommand command = new SendQueryCommand(1L, 999L, "질문", null, null);
        when(loadChatRoomPort.findActiveByIdAndUserId(999L, 1L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.prepare(command))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.CHAT_ROOM_NOT_FOUND));
    }

    @Test
    @DisplayName("prepare() - 다른 사용자의 roomId를 지정하면 CHAT_ROOM_NOT_FOUND 예외를 던진다 (IDOR 방지)")
    void prepare_otherUserRoom_throwsChatRoomNotFound() {
        Long requestingUserId = 1L;
        Long otherUsersRoomId = 42L;
        SendQueryCommand command = new SendQueryCommand(requestingUserId, otherUsersRoomId, "질문", null, null);

        // findActiveByIdAndUserId는 소유자 불일치 시 empty를 반환한다
        when(loadChatRoomPort.findActiveByIdAndUserId(otherUsersRoomId, requestingUserId))
                .thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.prepare(command))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.CHAT_ROOM_NOT_FOUND));

        verify(loadChatRoomPort).findActiveByIdAndUserId(otherUsersRoomId, requestingUserId);
        verify(saveChatRoomPort, never()).save(any());
    }

    @Test
    @DisplayName("prepare() - 직전 N턴이 USER→user/ASSISTANT→assistant로 매핑되어 history에 담긴다(과거→최신)")
    void prepare_buildsHistoryFromRecentMessages() {
        Long userId = 1L;
        Long roomId = 5L;
        SendQueryCommand command = new SendQueryCommand(userId, roomId, "그 중 무료인 것만", null, null);

        when(loadChatRoomPort.findActiveByIdAndUserId(roomId, userId)).thenReturn(Optional.of(savedRoom(roomId)));
        when(loadChatMessagePort.findRecentByRoomIdOrderBySeqAsc(roomId, 10)).thenReturn(List.of(
                msg(roomId, 1L, ChatMessageRole.USER, "강남구 문화행사 알려줘"),
                msg(roomId, 2L, ChatMessageRole.ASSISTANT, "강남구 문화행사 5건을 안내합니다.")));
        when(saveChatMessagePort.nextSeq()).thenReturn(3L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        PrepareResult result = service.prepare(command);

        assertThat(result.history()).containsExactly(
                new ChatTurn("user", "강남구 문화행사 알려줘"),
                new ChatTurn("assistant", "강남구 문화행사 5건을 안내합니다."));
    }

    @Test
    @DisplayName("prepare() - 현재 질문은 USER 저장 전에 history를 조립하므로 history에 포함되지 않는다")
    void prepare_currentQuestionExcludedFromHistory() {
        Long userId = 1L;
        Long roomId = 5L;
        String currentQuestion = "현재 질문";
        SendQueryCommand command = new SendQueryCommand(userId, roomId, currentQuestion, null, null);

        when(loadChatRoomPort.findActiveByIdAndUserId(roomId, userId)).thenReturn(Optional.of(savedRoom(roomId)));
        when(loadChatMessagePort.findRecentByRoomIdOrderBySeqAsc(roomId, 10)).thenReturn(List.of(
                msg(roomId, 1L, ChatMessageRole.USER, "이전 질문")));
        when(saveChatMessagePort.nextSeq()).thenReturn(2L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        PrepareResult result = service.prepare(command);

        assertThat(result.history()).extracting(ChatTurn::content).doesNotContain(currentQuestion);
        // history 조립은 USER 저장보다 먼저 일어나야 한다
        InOrder order = inOrder(loadChatMessagePort, saveChatMessagePort);
        order.verify(loadChatMessagePort).findRecentByRoomIdOrderBySeqAsc(roomId, 10);
        order.verify(saveChatMessagePort).save(any(ChatMessage.class));
    }

    @Test
    @DisplayName("prepare() - maxTurns=5이면 윈도우 한도로 maxMessages=10을 포트에 전달하고 10메시지를 과거→최신으로 담는다")
    void prepare_fiveTurnWindow_requestsTenMessagesAndMapsAll() {
        Long userId = 1L;
        Long roomId = 5L;
        SendQueryCommand command = new SendQueryCommand(userId, roomId, "현재 질문", null, null);

        when(loadChatRoomPort.findActiveByIdAndUserId(roomId, userId)).thenReturn(Optional.of(savedRoom(roomId)));
        // 정확히 5턴(=10메시지)을 과거 → 최신으로 구성
        java.util.List<ChatMessage> tenMessages = new java.util.ArrayList<>();
        for (int turn = 0; turn < 5; turn++) {
            tenMessages.add(msg(roomId, turn * 2 + 1, ChatMessageRole.USER, "Q" + turn));
            tenMessages.add(msg(roomId, turn * 2 + 2, ChatMessageRole.ASSISTANT, "A" + turn));
        }
        when(loadChatMessagePort.findRecentByRoomIdOrderBySeqAsc(roomId, 10)).thenReturn(tenMessages);
        when(saveChatMessagePort.nextSeq()).thenReturn(11L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        PrepareResult result = service.prepare(command);

        // 윈도우 한도는 maxTurns(5) * 2 = 10 이어야 한다
        verify(loadChatMessagePort).findRecentByRoomIdOrderBySeqAsc(roomId, 10);
        assertThat(result.history()).hasSize(10);
        assertThat(result.history().get(0)).isEqualTo(new ChatTurn("user", "Q0"));
        assertThat(result.history().get(9)).isEqualTo(new ChatTurn("assistant", "A4"));
        // 순서 검증: 과거 → 최신, role 교대
        assertThat(result.history()).extracting(ChatTurn::role)
                .containsExactly("user", "assistant", "user", "assistant", "user",
                        "assistant", "user", "assistant", "user", "assistant");
    }

    @Test
    @DisplayName("prepare() - 짝이 맞지 않는 orphan USER 메시지도 쌍을 강제하지 않고 존재하는 메시지만 그대로 전달한다")
    void prepare_orphanUserMessage_passedThroughWithoutPairing() {
        Long userId = 1L;
        Long roomId = 5L;
        SendQueryCommand command = new SendQueryCommand(userId, roomId, "후속 질문", null, null);

        when(loadChatRoomPort.findActiveByIdAndUserId(roomId, userId)).thenReturn(Optional.of(savedRoom(roomId)));
        // ASSISTANT 답변이 누락된 채 USER 메시지만 존재(예: 직전 스트림 실패)
        when(loadChatMessagePort.findRecentByRoomIdOrderBySeqAsc(roomId, 10)).thenReturn(List.of(
                msg(roomId, 1L, ChatMessageRole.USER, "이전 질문 A"),
                msg(roomId, 2L, ChatMessageRole.ASSISTANT, "이전 답변 A"),
                msg(roomId, 3L, ChatMessageRole.USER, "답변 못 받은 질문")));
        when(saveChatMessagePort.nextSeq()).thenReturn(4L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        PrepareResult result = service.prepare(command);

        // 쌍을 맞추려 마지막 orphan USER를 버리지 않는다
        assertThat(result.history()).containsExactly(
                new ChatTurn("user", "이전 질문 A"),
                new ChatTurn("assistant", "이전 답변 A"),
                new ChatTurn("user", "답변 못 받은 질문"));
    }

    @Test
    @DisplayName("prepare() - 새 방이면 history는 빈 리스트다")
    void prepare_newRoom_emptyHistory() {
        SendQueryCommand command = new SendQueryCommand(1L, null, "첫 질문", null, null);
        when(saveChatRoomPort.save(any(ChatRoom.class))).thenReturn(savedRoom(10L));
        when(loadChatMessagePort.findRecentByRoomIdOrderBySeqAsc(10L, 10)).thenReturn(List.of());
        when(saveChatMessagePort.nextSeq()).thenReturn(1L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        PrepareResult result = service.prepare(command);

        assertThat(result.history()).isEmpty();
    }

    @Test
    @DisplayName("prepare() - content가 길이 캡을 초과하면 truncate된다")
    void prepare_longContent_truncatedToCap() {
        ChatHistoryProperties cap5 = new ChatHistoryProperties(5, 5);
        service = new SendQueryService(saveChatRoomPort, loadChatRoomPort, saveChatMessagePort,
                loadChatMessagePort, cap5);

        Long roomId = 5L;
        SendQueryCommand command = new SendQueryCommand(1L, roomId, "질문", null, null);
        when(loadChatRoomPort.findActiveByIdAndUserId(roomId, 1L)).thenReturn(Optional.of(savedRoom(roomId)));
        when(loadChatMessagePort.findRecentByRoomIdOrderBySeqAsc(roomId, 10)).thenReturn(List.of(
                msg(roomId, 1L, ChatMessageRole.ASSISTANT, "0123456789")));
        when(saveChatMessagePort.nextSeq()).thenReturn(2L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        PrepareResult result = service.prepare(command);

        assertThat(result.history()).hasSize(1);
        assertThat(result.history().get(0).content()).isEqualTo("01234");
    }

    @Test
    @DisplayName("prepare() - 이모지(surrogate pair) 경계에서 truncate해도 깨진 문자 없이 코드포인트 단위로 잘린다")
    void prepare_emojiContent_truncatedWithoutBrokenSurrogate() {
        // maxCharsPerMessage=3: 이모지 3개(각 surrogate pair, char 6개)까지만 남아야 한다
        ChatHistoryProperties cap3 = new ChatHistoryProperties(5, 3);
        service = new SendQueryService(saveChatRoomPort, loadChatRoomPort, saveChatMessagePort,
                loadChatMessagePort, cap3);

        Long roomId = 5L;
        SendQueryCommand command = new SendQueryCommand(1L, roomId, "질문", null, null);
        when(loadChatRoomPort.findActiveByIdAndUserId(roomId, 1L)).thenReturn(Optional.of(savedRoom(roomId)));
        // 이모지 5개(😀😁😂😃😄), 각각 BMP 밖 코드포인트(surrogate pair)
        String emojis = "😀😁😂😃😄";
        when(loadChatMessagePort.findRecentByRoomIdOrderBySeqAsc(roomId, 10)).thenReturn(List.of(
                msg(roomId, 1L, ChatMessageRole.ASSISTANT, emojis)));
        when(saveChatMessagePort.nextSeq()).thenReturn(2L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        PrepareResult result = service.prepare(command);

        String truncated = result.history().get(0).content();
        // 코드포인트 3개("😀😁😂") = char 6개로 잘려야 한다
        assertThat(truncated.codePointCount(0, truncated.length())).isEqualTo(3);
        assertThat(truncated).isEqualTo("😀😁😂");
        // 끝에 외톨이 high-surrogate가 남지 않아야 한다
        assertThat(Character.isHighSurrogate(truncated.charAt(truncated.length() - 1))).isFalse();
    }

    @Test
    @DisplayName("prepare() - 캡 미만 길이의 이모지 문자열은 그대로 유지된다")
    void prepare_emojiContentUnderCap_keptAsIs() {
        ChatHistoryProperties cap10 = new ChatHistoryProperties(5, 10);
        service = new SendQueryService(saveChatRoomPort, loadChatRoomPort, saveChatMessagePort,
                loadChatMessagePort, cap10);

        Long roomId = 5L;
        SendQueryCommand command = new SendQueryCommand(1L, roomId, "질문", null, null);
        when(loadChatRoomPort.findActiveByIdAndUserId(roomId, 1L)).thenReturn(Optional.of(savedRoom(roomId)));
        String emojis = "😀😁"; // 코드포인트 2개 (< 캡 10)
        when(loadChatMessagePort.findRecentByRoomIdOrderBySeqAsc(roomId, 10)).thenReturn(List.of(
                msg(roomId, 1L, ChatMessageRole.ASSISTANT, emojis)));
        when(saveChatMessagePort.nextSeq()).thenReturn(2L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        PrepareResult result = service.prepare(command);

        assertThat(result.history().get(0).content()).isEqualTo(emojis);
    }

    @Test
    @DisplayName("prepare() - history 조회가 예외를 던지면 빈 리스트로 폴백하고 USER 저장은 계속된다")
    void prepare_historyLoadFails_fallsBackToEmpty() {
        Long roomId = 5L;
        SendQueryCommand command = new SendQueryCommand(1L, roomId, "질문", null, null);
        when(loadChatRoomPort.findActiveByIdAndUserId(roomId, 1L)).thenReturn(Optional.of(savedRoom(roomId)));
        when(loadChatMessagePort.findRecentByRoomIdOrderBySeqAsc(roomId, 10))
                .thenThrow(new RuntimeException("DB 장애"));
        when(saveChatMessagePort.nextSeq()).thenReturn(2L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        PrepareResult result = service.prepare(command);

        assertThat(result.history()).isEmpty();
        verify(saveChatMessagePort).save(any(ChatMessage.class));
    }

    @Test
    @DisplayName("saveAnswer() - ASSISTANT 메시지를 저장한다")
    void saveAnswer_savesAssistantMessage() {
        Long roomId = 10L;
        String answer = "서울 문화행사는 다음과 같습니다.";

        when(saveChatMessagePort.nextSeq()).thenReturn(3L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        service.saveAnswer(roomId, answer);

        ArgumentCaptor<ChatMessage> captor = ArgumentCaptor.forClass(ChatMessage.class);
        verify(saveChatMessagePort).save(captor.capture());
        assertThat(captor.getValue().getRole()).isEqualTo(ChatMessageRole.ASSISTANT);
        assertThat(captor.getValue().getContent()).isEqualTo(answer);
        assertThat(captor.getValue().getRoomId()).isEqualTo(roomId);
        assertThat(captor.getValue().getSeq()).isEqualTo(3L);
    }
}
