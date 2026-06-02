package dev.jazzybyte.onseoul.chat;

import dev.jazzybyte.onseoul.chat.adapter.in.web.ChatController;
import dev.jazzybyte.onseoul.shared.adapter.in.web.GlobalExceptionHandler;
import dev.jazzybyte.onseoul.chat.port.in.QueryAndStreamUseCase;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.security.servlet.SecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityFilterAutoConfiguration;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import reactor.core.publisher.Flux;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@WebMvcTest(controllers = {ChatController.class, GlobalExceptionHandler.class},
        excludeAutoConfiguration = {
                SecurityAutoConfiguration.class,
                SecurityFilterAutoConfiguration.class,
                org.springframework.boot.autoconfigure.security.oauth2.client.servlet.OAuth2ClientAutoConfiguration.class,
                org.springframework.boot.autoconfigure.security.oauth2.client.servlet.OAuth2ClientWebSecurityAutoConfiguration.class
        })
class ChatControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private QueryAndStreamUseCase queryAndStreamUseCase;

    @Test
    @DisplayName("POST /api/chat/query - 정상 질의 시 SSE 토큰을 스트리밍한다")
    void query_authenticatedUser_streamsTokens() throws Exception {
        Long userId = 1L;

        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenReturn(Flux.just("안녕", "하세요"));

        mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", userId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"서울 문화행사 알려줘\"}"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM));

        verify(queryAndStreamUseCase).streamAndSave(any(SendQueryCommand.class));
    }

    @Test
    @DisplayName("POST /api/chat/query - roomId가 포함된 요청은 기존 방 ID를 커맨드에 전달한다")
    void query_withExistingRoomId_passesRoomIdInCommand() throws Exception {
        Long userId = 1L;
        Long roomId = 5L;

        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenReturn(Flux.just("답변"));

        mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", userId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"roomId\":5,\"question\":\"추가 질문\"}"))
                .andExpect(status().isOk());

        verify(queryAndStreamUseCase).streamAndSave(argThat(cmd ->
                cmd.userId().equals(userId) &&
                cmd.roomId().equals(roomId) &&
                cmd.question().equals("추가 질문")));
    }

    @Test
    @DisplayName("POST /api/chat/query - question이 빈 문자열이면 400과 GlobalExceptionHandler 형식의 JSON 바디를 반환한다")
    void query_emptyQuestion_returns400WithErrorBody() throws Exception {
        mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.code").value("잘못된 요청값"))
                .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("question")));
    }

    @Test
    @DisplayName("POST /api/chat/query - question 필드 자체가 없으면 400과 GlobalExceptionHandler 형식의 JSON 바디를 반환한다")
    void query_nullQuestion_returns400WithErrorBody() throws Exception {
        mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.code").value("잘못된 요청값"))
                .andExpect(jsonPath("$.message").value(org.hamcrest.Matchers.containsString("question")));
    }

    @Test
    @DisplayName("POST /api/chat/query - AI 서비스 오류 시 SSE error 이벤트를 전송한다")
    void query_aiServiceError_sendsErrorEvent() throws Exception {
        Long userId = 1L;

        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenReturn(Flux.error(new OnSeoulApiException(
                        ErrorCode.AI_SERVICE_ERROR, "AI 서비스 스트림 오류: connection refused")));

        mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", userId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"서울 문화행사 알려줘\"}"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM))
                .andExpect(content().string(org.hamcrest.Matchers.containsString("event:error")));

        verify(queryAndStreamUseCase).streamAndSave(any(SendQueryCommand.class));
    }
}
