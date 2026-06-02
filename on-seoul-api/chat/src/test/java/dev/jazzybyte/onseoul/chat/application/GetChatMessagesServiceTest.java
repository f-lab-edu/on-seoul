package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatMessageRole;
import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.port.in.GetChatMessagesUseCase.ChatHistory;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class GetChatMessagesServiceTest {

    @Mock private LoadChatRoomPort loadChatRoomPort;
    @Mock private LoadChatMessagePort loadChatMessagePort;

    private GetChatMessagesService service;

    @BeforeEach
    void setUp() {
        service = new GetChatMessagesService(loadChatRoomPort, loadChatMessagePort);
    }

    private ChatRoom activeRoom(Long id, Long userId) {
        return new ChatRoom(id, userId, "제목", false,
                OffsetDateTime.now(), OffsetDateTime.now(), null);
    }

    private ChatMessage message(Long roomId, Long seq) {
        return new ChatMessage(seq, roomId, seq, ChatMessageRole.USER, "내용", OffsetDateTime.now());
    }

    @Test
    @DisplayName("get() - 존재하는 방이면 방과 메시지 목록을 반환한다")
    void get_existingRoom_returnsHistory() {
        Long userId = 1L;
        Long roomId = 10L;
        ChatRoom room = activeRoom(roomId, userId);
        List<ChatMessage> messages = List.of(message(roomId, 1L), message(roomId, 2L));

        when(loadChatRoomPort.findActiveByIdAndUserId(roomId, userId)).thenReturn(Optional.of(room));
        when(loadChatMessagePort.findByRoomIdOrderBySeqAsc(roomId)).thenReturn(messages);

        ChatHistory history = service.get(userId, roomId);

        assertThat(history.room()).isEqualTo(room);
        assertThat(history.messages()).hasSize(2);
    }

    @Test
    @DisplayName("get() - 방이 존재하지 않으면 CHAT_ROOM_NOT_FOUND 예외를 던진다")
    void get_roomNotFound_throwsChatRoomNotFound() {
        when(loadChatRoomPort.findActiveByIdAndUserId(99L, 1L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.get(1L, 99L))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.CHAT_ROOM_NOT_FOUND));
    }

    @Test
    @DisplayName("get() - 삭제됐거나 다른 유저의 방이면 포트가 empty를 반환하여 CHAT_ROOM_NOT_FOUND 예외를 던진다")
    void get_deletedOrOtherUserRoom_throwsChatRoomNotFound() {
        when(loadChatRoomPort.findActiveByIdAndUserId(10L, 2L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.get(2L, 10L))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.CHAT_ROOM_NOT_FOUND));
    }
}
