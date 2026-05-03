package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.model.ChatMessage;
import dev.jazzybyte.onseoul.domain.model.ChatMessageRole;
import dev.jazzybyte.onseoul.domain.model.ChatRoom;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.domain.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.domain.port.out.SaveChatMessagePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveChatRoomPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.OffsetDateTime;
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

    private SendQueryService service;

    @BeforeEach
    void setUp() {
        service = new SendQueryService(saveChatRoomPort, loadChatRoomPort, saveChatMessagePort);
    }

    private ChatRoom savedRoom(Long id) {
        return new ChatRoom(id, 1L, "질문 제목", false,
                OffsetDateTime.now(), OffsetDateTime.now());
    }

    @Test
    @DisplayName("prepare() - roomId가 null이면 새 ChatRoom을 생성하고 USER 메시지를 저장한 뒤 roomId를 반환한다")
    void prepare_newRoom_createsRoomAndSavesUserMessage() {
        Long userId = 1L;
        String question = "서울 문화행사 알려줘";
        SendQueryCommand command = new SendQueryCommand(userId, null, question);

        ChatRoom createdRoom = savedRoom(10L);
        when(saveChatRoomPort.save(any(ChatRoom.class))).thenReturn(createdRoom);
        when(saveChatMessagePort.nextSeq()).thenReturn(1L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        Long roomId = service.prepare(command);

        assertThat(roomId).isEqualTo(10L);

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
        SendQueryCommand command = new SendQueryCommand(1L, null, longQuestion);

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
        SendQueryCommand command = new SendQueryCommand(userId, existingRoomId, question);

        ChatRoom existingRoom = savedRoom(existingRoomId);
        when(loadChatRoomPort.findById(existingRoomId)).thenReturn(Optional.of(existingRoom));
        when(saveChatMessagePort.nextSeq()).thenReturn(2L);
        when(saveChatMessagePort.save(any(ChatMessage.class))).thenAnswer(inv -> inv.getArgument(0));

        Long roomId = service.prepare(command);

        assertThat(roomId).isEqualTo(existingRoomId);
        verify(saveChatRoomPort, never()).save(any());

        ArgumentCaptor<ChatMessage> msgCaptor = ArgumentCaptor.forClass(ChatMessage.class);
        verify(saveChatMessagePort).save(msgCaptor.capture());
        assertThat(msgCaptor.getValue().getRole()).isEqualTo(ChatMessageRole.USER);
        assertThat(msgCaptor.getValue().getRoomId()).isEqualTo(existingRoomId);
    }

    @Test
    @DisplayName("prepare() - roomId가 주어졌지만 존재하지 않으면 CHAT_ROOM_NOT_FOUND 예외를 던진다")
    void prepare_roomNotFound_throwsException() {
        SendQueryCommand command = new SendQueryCommand(1L, 999L, "질문");
        when(loadChatRoomPort.findById(999L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.prepare(command))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.CHAT_ROOM_NOT_FOUND));
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
