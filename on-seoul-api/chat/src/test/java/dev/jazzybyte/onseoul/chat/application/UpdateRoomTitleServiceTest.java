package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatRoomPort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.OffsetDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class UpdateRoomTitleServiceTest {

    @Mock private LoadChatRoomPort loadChatRoomPort;
    @Mock private SaveChatRoomPort saveChatRoomPort;

    private UpdateRoomTitleService service;

    @BeforeEach
    void setUp() {
        service = new UpdateRoomTitleService(loadChatRoomPort, saveChatRoomPort);
    }

    private ChatRoom room(Long id, boolean titleGenerated) {
        return room(id, titleGenerated, null);
    }

    private ChatRoom room(Long id, boolean titleGenerated, OffsetDateTime deletedAt) {
        return new ChatRoom(id, 1L, "원본 50자 폴백 제목", titleGenerated,
                OffsetDateTime.now(), OffsetDateTime.now(), deletedAt);
    }

    @Test
    @DisplayName("updateRoomTitle() - 폴백 제목 방(titleGenerated=false)이면 제목을 갱신하고 저장한다")
    void updateRoomTitle_freshRoom_updatesAndSaves() {
        ChatRoom room = room(42L, false);
        when(loadChatRoomPort.findById(42L)).thenReturn(Optional.of(room));

        service.updateRoomTitle(42L, "AI 생성 제목");

        assertThat(room.getTitle()).isEqualTo("AI 생성 제목");
        assertThat(room.isTitleGenerated()).isTrue();
        // titleGenerated == true로 전이된 ChatRoom으로 저장되는지 단언(영속 직전 상태 검증).
        verify(saveChatRoomPort).save(argThat(saved ->
                saved == room && saved.isTitleGenerated() && "AI 생성 제목".equals(saved.getTitle())));
    }

    @Test
    @DisplayName("updateRoomTitle() - soft-delete된 방이면 제목을 갱신하지 않고 스킵한다(save 미호출)")
    void updateRoomTitle_softDeletedRoom_skips() {
        ChatRoom room = room(42L, false, OffsetDateTime.now());
        when(loadChatRoomPort.findById(42L)).thenReturn(Optional.of(room));

        service.updateRoomTitle(42L, "AI 생성 제목");

        assertThat(room.getTitle()).isEqualTo("원본 50자 폴백 제목");
        assertThat(room.isTitleGenerated()).isFalse();
        verify(saveChatRoomPort, never()).save(any());
    }

    @Test
    @DisplayName("updateRoomTitle() - 200자(코드포인트) 초과 제목은 200자로 truncate되어 저장된다(VARCHAR(200) 방어)")
    void updateRoomTitle_overLongTitle_truncatedTo200() {
        ChatRoom room = room(42L, false);
        when(loadChatRoomPort.findById(42L)).thenReturn(Optional.of(room));
        String longTitle = "가".repeat(250);

        service.updateRoomTitle(42L, longTitle);

        assertThat(room.getTitle().codePointCount(0, room.getTitle().length())).isEqualTo(200);
        verify(saveChatRoomPort).save(room);
    }

    @Test
    @DisplayName("updateRoomTitle() - 이미 AI 생성 제목이 있는 방(titleGenerated=true)은 멱등 스킵한다")
    void updateRoomTitle_alreadyGenerated_skips() {
        ChatRoom room = room(42L, true);
        when(loadChatRoomPort.findById(42L)).thenReturn(Optional.of(room));

        service.updateRoomTitle(42L, "다른 제목");

        assertThat(room.getTitle()).isEqualTo("원본 50자 폴백 제목");
        verify(saveChatRoomPort, never()).save(any());
    }

    @Test
    @DisplayName("updateRoomTitle() - blank 제목이면 조회조차 하지 않고 즉시 반환한다(폴백 제목 보호)")
    void updateRoomTitle_blankTitle_returnsImmediately() {
        service.updateRoomTitle(42L, "   ");

        verifyNoInteractions(loadChatRoomPort);
        verifyNoInteractions(saveChatRoomPort);
    }

    @Test
    @DisplayName("updateRoomTitle() - null 제목이면 즉시 반환한다")
    void updateRoomTitle_nullTitle_returnsImmediately() {
        service.updateRoomTitle(42L, null);

        verifyNoInteractions(loadChatRoomPort);
        verifyNoInteractions(saveChatRoomPort);
    }

    @Test
    @DisplayName("updateRoomTitle() - 방이 없으면 에러 없이 조용히 무시한다(fail-open)")
    void updateRoomTitle_roomNotFound_noError() {
        when(loadChatRoomPort.findById(99L)).thenReturn(Optional.empty());

        service.updateRoomTitle(99L, "제목");

        verify(saveChatRoomPort, never()).save(any());
    }
}
