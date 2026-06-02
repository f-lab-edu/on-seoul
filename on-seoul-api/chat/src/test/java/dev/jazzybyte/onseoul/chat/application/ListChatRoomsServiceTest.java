package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.port.in.ListChatRoomsUseCase.RoomPage;
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
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ListChatRoomsServiceTest {

    @Mock private LoadChatRoomPort loadChatRoomPort;

    private ListChatRoomsService service;

    @BeforeEach
    void setUp() {
        service = new ListChatRoomsService(loadChatRoomPort);
    }

    private ChatRoom room(Long id) {
        return new ChatRoom(id, 1L, "제목" + id, false,
                OffsetDateTime.now(), OffsetDateTime.now(), null);
    }

    private List<ChatRoom> rooms(int count) {
        List<ChatRoom> list = new ArrayList<>();
        for (int i = 1; i <= count; i++) {
            list.add(room((long) i));
        }
        return list;
    }

    @Test
    @DisplayName("list() - size+1건 반환 시 rooms에 size건이 담기고 nextCursor가 비null이다")
    void list_firstPage_returnsRoomsWithNextCursor() {
        int size = 5;
        List<ChatRoom> fetched = rooms(size + 1);
        when(loadChatRoomPort.findActiveByUserId(eq(1L), any(), eq(size + 1))).thenReturn(fetched);

        RoomPage page = service.list(1L, null, size);

        assertThat(page.rooms()).hasSize(size);
        assertThat(page.nextCursor()).isNotNull();
    }

    @Test
    @DisplayName("list() - size 이하 반환 시 nextCursor가 null이다")
    void list_lastPage_returnsNullNextCursor() {
        int size = 5;
        List<ChatRoom> fetched = rooms(3);
        when(loadChatRoomPort.findActiveByUserId(eq(1L), any(), eq(size + 1))).thenReturn(fetched);

        RoomPage page = service.list(1L, null, size);

        assertThat(page.rooms()).hasSize(3);
        assertThat(page.nextCursor()).isNull();
    }

    @Test
    @DisplayName("list() - size=0이면 1로 clamp되어 포트가 limit=2로 호출된다")
    void list_sizeClamped_belowMin() {
        when(loadChatRoomPort.findActiveByUserId(eq(1L), any(), eq(2))).thenReturn(List.of());

        service.list(1L, null, 0);

        verify(loadChatRoomPort).findActiveByUserId(eq(1L), any(), eq(2));
    }

    @Test
    @DisplayName("list() - size=200이면 100으로 clamp되어 포트가 limit=101로 호출된다")
    void list_sizeClamped_aboveMax() {
        when(loadChatRoomPort.findActiveByUserId(eq(1L), any(), eq(101))).thenReturn(List.of());

        service.list(1L, null, 200);

        verify(loadChatRoomPort).findActiveByUserId(eq(1L), any(), eq(101));
    }

    @Test
    @DisplayName("list() - 결과가 정확히 size건이면 nextCursor가 null이다 (마지막 페이지)")
    void list_exactSizeResult_returnsNullNextCursor() {
        int size = 5;
        List<ChatRoom> fetched = rooms(size); // 딱 size건 — size+1 미만이므로 마지막 페이지
        when(loadChatRoomPort.findActiveByUserId(eq(1L), any(), eq(size + 1))).thenReturn(fetched);

        RoomPage page = service.list(1L, null, size);

        assertThat(page.rooms()).hasSize(size);
        assertThat(page.nextCursor()).isNull();
    }

    @Test
    @DisplayName("list() - 잘못된 cursor 문자열이면 INVALID_INPUT 예외를 던진다")
    void list_invalidCursor_throwsInvalidInput() {
        assertThatThrownBy(() -> service.list(1L, "not-valid-base64!!!", 10))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.INVALID_INPUT));
    }
}
