package dev.jazzybyte.onseoul.chat;

import dev.jazzybyte.onseoul.chat.adapter.in.web.ChatHistoryController;
import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatMessageRole;
import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.port.in.DeleteChatRoomUseCase;
import dev.jazzybyte.onseoul.chat.port.in.GetChatMessagesUseCase;
import dev.jazzybyte.onseoul.chat.port.in.ListChatRoomsUseCase;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.shared.adapter.in.web.GlobalExceptionHandler;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.security.oauth2.client.servlet.OAuth2ClientAutoConfiguration;
import org.springframework.boot.autoconfigure.security.oauth2.client.servlet.OAuth2ClientWebSecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityFilterAutoConfiguration;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.time.OffsetDateTime;
import java.util.List;

import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doNothing;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(controllers = {ChatHistoryController.class, GlobalExceptionHandler.class},
        excludeAutoConfiguration = {
                SecurityAutoConfiguration.class,
                SecurityFilterAutoConfiguration.class,
                OAuth2ClientAutoConfiguration.class,
                OAuth2ClientWebSecurityAutoConfiguration.class
        })
class ChatHistoryControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private ListChatRoomsUseCase listChatRoomsUseCase;

    @MockitoBean
    private GetChatMessagesUseCase getChatMessagesUseCase;

    @MockitoBean
    private DeleteChatRoomUseCase deleteChatRoomUseCase;

    // ── GET /api/chat/rooms ───────────────────────────────────────────────

    @Test
    @DisplayName("GET /api/chat/rooms - userId 주입 + cursor/size → 200 rooms + nextCursor")
    void listRooms_authenticated_returns200() throws Exception {
        ChatRoom room = new ChatRoom(1L, 10L, "서울 문화행사", false,
                OffsetDateTime.now(), OffsetDateTime.now(), null);
        ListChatRoomsUseCase.RoomPage page = new ListChatRoomsUseCase.RoomPage(List.of(room), "cursor-next");

        when(listChatRoomsUseCase.list(eq(10L), eq("cursor-abc"), eq(10)))
                .thenReturn(page);

        mockMvc.perform(get("/api/chat/rooms")
                        .requestAttr("userId", 10L)
                        .param("cursor", "cursor-abc")
                        .param("size", "10"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.rooms[0].roomId").value(1))
                .andExpect(jsonPath("$.rooms[0].title").value("서울 문화행사"))
                .andExpect(jsonPath("$.rooms[0].titleGenerated").value(false))
                .andExpect(jsonPath("$.nextCursor").value("cursor-next"));
    }

    @Test
    @DisplayName("GET /api/chat/rooms - userId null → 401")
    void listRooms_unauthenticated_returns401() throws Exception {
        mockMvc.perform(get("/api/chat/rooms"))
                .andExpect(status().isUnauthorized());
    }

    // ── GET /api/chat/rooms/{roomId}/messages ────────────────────────────

    @Test
    @DisplayName("GET /api/chat/rooms/{roomId}/messages - 정상 roomId + userId → 200 roomId/title/messages")
    void getMessages_authenticated_returns200() throws Exception {
        ChatRoom room = new ChatRoom(5L, 10L, "체육시설 문의", false,
                OffsetDateTime.now(), OffsetDateTime.now(), null);
        ChatMessage msg = new ChatMessage(1L, 5L, 1L, ChatMessageRole.USER, "안녕하세요", OffsetDateTime.now());
        GetChatMessagesUseCase.ChatHistory history = new GetChatMessagesUseCase.ChatHistory(room, List.of(msg));

        when(getChatMessagesUseCase.get(eq(10L), eq(5L))).thenReturn(history);

        mockMvc.perform(get("/api/chat/rooms/5/messages")
                        .requestAttr("userId", 10L))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.roomId").value(5))
                .andExpect(jsonPath("$.title").value("체육시설 문의"))
                .andExpect(jsonPath("$.messages[0].seq").value(1))
                .andExpect(jsonPath("$.messages[0].role").value("USER"))
                .andExpect(jsonPath("$.messages[0].content").value("안녕하세요"));
    }

    @Test
    @DisplayName("GET /api/chat/rooms/{roomId}/messages - UseCase가 CHAT_ROOM_NOT_FOUND 예외 → 404")
    void getMessages_roomNotFound_returns404() throws Exception {
        when(getChatMessagesUseCase.get(eq(10L), eq(99L)))
                .thenThrow(new OnSeoulApiException(ErrorCode.CHAT_ROOM_NOT_FOUND));

        mockMvc.perform(get("/api/chat/rooms/99/messages")
                        .requestAttr("userId", 10L))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("CHAT_ROOM_NOT_FOUND"));
    }

    // ── DELETE /api/chat/rooms/{roomId} ──────────────────────────────────

    @Test
    @DisplayName("DELETE /api/chat/rooms/{roomId} - 정상 삭제 → 204 No Content")
    void deleteRoom_authenticated_returns204() throws Exception {
        doNothing().when(deleteChatRoomUseCase).delete(eq(10L), eq(5L));

        mockMvc.perform(delete("/api/chat/rooms/5")
                        .requestAttr("userId", 10L))
                .andExpect(status().isNoContent());

        verify(deleteChatRoomUseCase).delete(10L, 5L);
    }

    @Test
    @DisplayName("DELETE /api/chat/rooms/{roomId} - UseCase가 CHAT_ROOM_NOT_FOUND 예외 → 404")
    void deleteRoom_roomNotFound_returns404() throws Exception {
        doThrow(new OnSeoulApiException(ErrorCode.CHAT_ROOM_NOT_FOUND))
                .when(deleteChatRoomUseCase).delete(eq(10L), eq(99L));

        mockMvc.perform(delete("/api/chat/rooms/99")
                        .requestAttr("userId", 10L))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("CHAT_ROOM_NOT_FOUND"));
    }

    @Test
    @DisplayName("DELETE /api/chat/rooms/{roomId} - userId null → 401")
    void deleteRoom_unauthenticated_returns401() throws Exception {
        mockMvc.perform(delete("/api/chat/rooms/5"))
                .andExpect(status().isUnauthorized());
    }
}
