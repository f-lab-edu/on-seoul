package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.port.out.DeleteChatRoomPort;
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
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class DeleteChatRoomServiceTest {

    @Mock private LoadChatRoomPort loadChatRoomPort;
    @Mock private DeleteChatRoomPort deleteChatRoomPort;

    private DeleteChatRoomService service;

    @BeforeEach
    void setUp() {
        service = new DeleteChatRoomService(loadChatRoomPort, deleteChatRoomPort);
    }

    private ChatRoom activeRoom(Long id, Long userId) {
        return new ChatRoom(id, userId, "제목", false,
                OffsetDateTime.now(), OffsetDateTime.now(), null);
    }

    @Test
    @DisplayName("delete() - 방이 존재하면 softDelete가 호출된다")
    void delete_existingRoom_callsSoftDelete() {
        Long userId = 1L;
        Long roomId = 10L;
        ChatRoom room = activeRoom(roomId, userId);

        when(loadChatRoomPort.findActiveByIdAndUserId(roomId, userId)).thenReturn(Optional.of(room));

        service.delete(userId, roomId);

        assertThat(room.isDeleted()).isTrue();
        verify(deleteChatRoomPort).softDelete(room);
    }

    @Test
    @DisplayName("delete() - 방이 존재하지 않으면 CHAT_ROOM_NOT_FOUND 예외를 던진다")
    void delete_roomNotFound_throwsChatRoomNotFound() {
        when(loadChatRoomPort.findActiveByIdAndUserId(99L, 1L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.delete(1L, 99L))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.CHAT_ROOM_NOT_FOUND));
    }

    @Test
    @DisplayName("delete() - 삭제됐거나 다른 유저의 방이면 포트가 empty를 반환하여 CHAT_ROOM_NOT_FOUND 예외를 던진다")
    void delete_alreadyDeletedRoom_throwsChatRoomNotFound() {
        when(loadChatRoomPort.findActiveByIdAndUserId(10L, 2L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.delete(2L, 10L))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.CHAT_ROOM_NOT_FOUND));
    }
}
