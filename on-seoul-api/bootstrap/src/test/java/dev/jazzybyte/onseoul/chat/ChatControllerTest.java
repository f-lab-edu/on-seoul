package dev.jazzybyte.onseoul.chat;

import dev.jazzybyte.onseoul.adapter.in.web.ChatController;
import dev.jazzybyte.onseoul.adapter.in.web.GlobalExceptionHandler;
import dev.jazzybyte.onseoul.domain.port.in.QueryAndStreamUseCase;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryCommand;
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
    @DisplayName("POST /query - 정상 질의 시 SSE 토큰을 스트리밍한다")
    void query_authenticatedUser_streamsTokens() throws Exception {
        Long userId = 1L;

        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenReturn(Flux.just("안녕", "하세요"));

        mockMvc.perform(post("/query")
                        .requestAttr("userId", userId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"서울 문화행사 알려줘\"}"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM));

        verify(queryAndStreamUseCase).streamAndSave(any(SendQueryCommand.class));
    }

    @Test
    @DisplayName("POST /query - roomId가 포함된 요청은 기존 방 ID를 커맨드에 전달한다")
    void query_withExistingRoomId_passesRoomIdInCommand() throws Exception {
        Long userId = 1L;
        Long roomId = 5L;

        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenReturn(Flux.just("답변"));

        mockMvc.perform(post("/query")
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
    @DisplayName("POST /query - question이 빈 문자열이면 400을 반환한다")
    void query_emptyQuestion_returns400() throws Exception {
        mockMvc.perform(post("/query")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("POST /query - question 필드 자체가 없으면 400을 반환한다")
    void query_nullQuestion_returns400() throws Exception {
        mockMvc.perform(post("/query")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("POST /query - AI 서비스 오류 시 SSE error 이벤트를 전송한다")
    void query_aiServiceError_sendsErrorEvent() throws Exception {
        Long userId = 1L;

        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenReturn(Flux.error(new OnSeoulApiException(
                        ErrorCode.AI_SERVICE_ERROR, "AI 서비스 스트림 오류: connection refused")));

        mockMvc.perform(post("/query")
                        .requestAttr("userId", userId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"서울 문화행사 알려줘\"}"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM))
                .andExpect(content().string(org.hamcrest.Matchers.containsString("event:error")));

        verify(queryAndStreamUseCase).streamAndSave(any(SendQueryCommand.class));
    }
}
