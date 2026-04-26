package dev.jazzybyte.onseoul.chat;

import dev.jazzybyte.onseoul.adapter.in.web.ChatController;
import dev.jazzybyte.onseoul.adapter.in.web.GlobalExceptionHandler;
import dev.jazzybyte.onseoul.adapter.out.aiservice.AiServicePort;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryUseCase;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.security.servlet.SecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityFilterAutoConfiguration;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import reactor.core.publisher.Flux;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
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
    private SendQueryUseCase sendQueryUseCase;

    @MockitoBean
    private AiServicePort aiServicePort;

    @Test
    @DisplayName("POST /query - 인증된 사용자 질의 시 SSE 토큰을 스트리밍하고 답변을 저장한다")
    void query_authenticatedUser_streamsTokensAndSavesAnswer() throws Exception {
        Long userId = 1L;
        Long roomId = 10L;

        when(sendQueryUseCase.prepare(any(SendQueryCommand.class))).thenReturn(roomId);
        when(aiServicePort.stream(anyString(), anyLong()))
                .thenReturn(Flux.just("안녕", "하세요"));
        doNothing().when(sendQueryUseCase).saveAnswer(anyLong(), anyString());

        mockMvc.perform(post("/query")
                        .requestAttr("userId", userId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"서울 문화행사 알려줘\"}"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM));

        verify(sendQueryUseCase).prepare(any(SendQueryCommand.class));
        verify(aiServicePort).stream("서울 문화행사 알려줘", roomId);
        verify(sendQueryUseCase).saveAnswer(eq(roomId), anyString());
    }

    @Test
    @DisplayName("POST /query - roomId가 포함된 요청은 기존 방에 메시지를 추가한다")
    void query_withExistingRoomId_usesExistingRoom() throws Exception {
        Long userId = 1L;
        Long roomId = 5L;

        when(sendQueryUseCase.prepare(any(SendQueryCommand.class))).thenReturn(roomId);
        when(aiServicePort.stream(anyString(), anyLong()))
                .thenReturn(Flux.just("답변"));
        doNothing().when(sendQueryUseCase).saveAnswer(anyLong(), anyString());

        mockMvc.perform(post("/query")
                        .requestAttr("userId", userId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"roomId\":5,\"question\":\"추가 질문\"}"))
                .andExpect(status().isOk());

        verify(sendQueryUseCase).prepare(argThat(cmd ->
                cmd.userId().equals(userId) &&
                cmd.roomId().equals(roomId) &&
                cmd.question().equals("추가 질문")));
    }

    @Test
    @DisplayName("POST /query - question이 없으면 400을 반환한다")
    void query_missingQuestion_returns400() throws Exception {
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
    @DisplayName("POST /query - AI 서비스 오류 시 SSE error 이벤트를 전송하고 emitter를 완료한다")
    void query_aiServiceError_sendsErrorEventAndCompletesEmitter() throws Exception {
        Long userId = 1L;
        Long roomId = 10L;

        when(sendQueryUseCase.prepare(any(SendQueryCommand.class))).thenReturn(roomId);
        when(aiServicePort.stream(anyString(), anyLong()))
                .thenReturn(Flux.error(new OnSeoulApiException(
                        ErrorCode.AI_SERVICE_ERROR, "AI 서비스 스트림 오류: connection refused")));

        mockMvc.perform(post("/query")
                        .requestAttr("userId", userId)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"서울 문화행사 알려줘\"}"))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM))
                .andExpect(content().string(org.hamcrest.Matchers.containsString("event:error")));

        verify(sendQueryUseCase).prepare(any(SendQueryCommand.class));
        verify(sendQueryUseCase, never()).saveAnswer(anyLong(), anyString());
    }
}
