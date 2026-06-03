package dev.jazzybyte.onseoul.chat;

import dev.jazzybyte.onseoul.chat.adapter.in.web.ChatController;
import dev.jazzybyte.onseoul.shared.adapter.in.web.GlobalExceptionHandler;
import dev.jazzybyte.onseoul.chat.port.in.QueryAndStreamUseCase;
import dev.jazzybyte.onseoul.chat.port.in.QueryAndStreamUseCase.StreamResult;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.hamcrest.Matchers;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.security.servlet.SecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityFilterAutoConfiguration;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import reactor.core.publisher.Flux;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.asyncDispatch;
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

    private static StreamResult result(long roomId, boolean created, Flux<String> tokens) {
        return new StreamResult(roomId, created, tokens);
    }

    @Test
    @DisplayName("POST /api/chat/query - 정상 질의 시 SSE 토큰을 스트리밍한다")
    void query_authenticatedUser_streamsTokens() throws Exception {
        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenReturn(result(10L, true, Flux.just("안녕", "하세요")));

        MvcResult mvc = mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"서울 문화행사 알려줘\"}"))
                .andExpect(request().asyncStarted())
                .andReturn();

        mockMvc.perform(asyncDispatch(mvc))
                .andExpect(status().isOk())
                .andExpect(content().contentTypeCompatibleWith(MediaType.TEXT_EVENT_STREAM));

        verify(queryAndStreamUseCase).streamAndSave(any(SendQueryCommand.class));
    }

    @Test
    @DisplayName("POST /api/chat/query - init 이벤트가 항상 첫 이벤트로 1회 전송된다(신규 created=true)")
    void query_newRoom_emitsInitEventFirstWithCreatedTrue() throws Exception {
        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenReturn(result(42L, true, Flux.just("토큰")));

        MvcResult mvc = mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"새 질문\"}"))
                .andExpect(request().asyncStarted())
                .andReturn();

        String body = mockMvc.perform(asyncDispatch(mvc))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();

        // init이 최초 이벤트이며 data가 snake_case JSON으로 직렬화된다
        assertThat(body, Matchers.containsString("event:init"));
        assertThat(body, Matchers.containsString("\"room_id\":42"));
        assertThat(body, Matchers.containsString("\"created\":true"));
        // init은 응답의 가장 앞 이벤트다
        assertThat(body.trim().startsWith("event:init"), Matchers.is(true));
        // init은 정확히 1회만 전송
        assertThat(countOccurrences(body, "event:init"), Matchers.is(1));
    }

    @Test
    @DisplayName("POST /api/chat/query - 기존 방이면 init 이벤트의 created=false")
    void query_existingRoom_emitsInitEventWithCreatedFalse() throws Exception {
        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenReturn(result(5L, false, Flux.just("답변")));

        MvcResult mvc = mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"roomId\":5,\"question\":\"추가 질문\"}"))
                .andExpect(request().asyncStarted())
                .andReturn();

        String body = mockMvc.perform(asyncDispatch(mvc))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();

        assertThat(body, Matchers.containsString("event:init"));
        assertThat(body, Matchers.containsString("\"room_id\":5"));
        assertThat(body, Matchers.containsString("\"created\":false"));
    }

    @Test
    @DisplayName("POST /api/chat/query - roomId가 포함된 요청은 기존 방 ID를 커맨드에 전달한다")
    void query_withExistingRoomId_passesRoomIdInCommand() throws Exception {
        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenReturn(result(5L, false, Flux.just("답변")));

        MvcResult mvc = mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"roomId\":5,\"question\":\"추가 질문\"}"))
                .andExpect(request().asyncStarted())
                .andReturn();

        mockMvc.perform(asyncDispatch(mvc)).andExpect(status().isOk());

        verify(queryAndStreamUseCase).streamAndSave(argThat(cmd ->
                cmd.userId().equals(1L) &&
                cmd.roomId().equals(5L) &&
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
                .andExpect(jsonPath("$.message").value(Matchers.containsString("question")));
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
                .andExpect(jsonPath("$.message").value(Matchers.containsString("question")));
    }

    @Test
    @DisplayName("POST /api/chat/query - 스트림 도중 AI 서비스 오류 시 SSE error 이벤트를 전송한다")
    void query_aiServiceError_sendsErrorEvent() throws Exception {
        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenReturn(result(10L, true, Flux.error(new OnSeoulApiException(
                        ErrorCode.AI_SERVICE_ERROR, "AI 서비스 스트림 오류: connection refused"))));

        MvcResult mvc = mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"서울 문화행사 알려줘\"}"))
                .andReturn();

        String body = dispatchAndReadBody(mvc);
        assertThat(body, Matchers.containsString("event:error"));
    }

    @Test
    @DisplayName("POST /api/chat/query - 스트림 도중 오류여도 init은 error보다 먼저 1회 전송된다(QA 보강)")
    void query_streamError_initStillPrecedesError() throws Exception {
        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenReturn(result(77L, true, Flux.error(new OnSeoulApiException(
                        ErrorCode.AI_SERVICE_ERROR, "AI 서비스 스트림 오류: connection refused"))));

        MvcResult mvc = mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"question\":\"서울 문화행사 알려줘\"}"))
                .andReturn();

        String body = dispatchAndReadBody(mvc);

        // prepare가 성공했으므로 init은 반드시 전송되고, 그 뒤에 error가 온다.
        assertThat(body, Matchers.containsString("event:init"));
        assertThat(body, Matchers.containsString("\"room_id\":77"));
        assertThat(body, Matchers.containsString("event:error"));
        assertThat(countOccurrences(body, "event:init"), Matchers.is(1));
        assertThat(body.indexOf("event:init") < body.indexOf("event:error"), Matchers.is(true));
    }

    @Test
    @DisplayName("POST /api/chat/query - init 뒤에 토큰 raw data가 가공 없이 그대로 relay된다(QA 보강)")
    void query_rawTokensRelayedVerbatimAfterInit() throws Exception {
        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                // getContentAsString()는 ISO-8859-1로 디코드하므로 verbatim 검증은 ASCII data로 한다
                // (한글 relay 무손실은 ChatStreamServiceTest StepVerifier가 담당).
                .thenReturn(result(3L, false, Flux.just(
                        "{\"stage\":\"routing\"}",
                        "{\"message_id\":9,\"answer\":\"hello\"}")));

        MvcResult mvc = mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"roomId\":3,\"question\":\"질문\"}"))
                .andExpect(request().asyncStarted())
                .andReturn();

        String body = mockMvc.perform(asyncDispatch(mvc))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();

        // 원본 JSON data가 변형(파싱/재직렬화) 없이 그대로 흐른다.
        assertThat(body, Matchers.containsString("{\"stage\":\"routing\"}"));
        assertThat(body, Matchers.containsString("{\"message_id\":9,\"answer\":\"hello\"}"));
        // init이 토큰들보다 앞선다.
        assertThat(body.indexOf("event:init") < body.indexOf("\"stage\":\"routing\""), Matchers.is(true));
    }

    @Test
    @DisplayName("POST /api/chat/query - prepare 실패(CHAT_ROOM_NOT_FOUND) 시 init 없이 error 이벤트만 전송한다")
    void query_prepareFails_emitsErrorWithoutInit() throws Exception {
        when(queryAndStreamUseCase.streamAndSave(any(SendQueryCommand.class)))
                .thenThrow(new OnSeoulApiException(ErrorCode.CHAT_ROOM_NOT_FOUND));

        // prepare 실패는 streamAndSave 호출에서 동기 throw → async 시작 전 즉시 SSE 완료
        MvcResult mvc = mockMvc.perform(post("/api/chat/query")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"roomId\":999,\"question\":\"질문\"}"))
                .andReturn();

        String body = dispatchAndReadBody(mvc);

        assertThat(body, Matchers.containsString("event:error"));
        assertThat(body, Matchers.not(Matchers.containsString("event:init")));
    }

    /**
     * SSE 응답 본문을 읽는다. emitter가 동기 완료(에러/즉시 종료)되면 async가 시작되지 않으므로
     * 그 경우 응답에서 바로 읽고, async가 시작되었으면 dispatch 후 읽는다. completeWithError로 인한
     * ServletException은 본문 검증에 영향을 주지 않으므로 무시한다.
     */
    private String dispatchAndReadBody(MvcResult mvc) throws Exception {
        if (mvc.getRequest().isAsyncStarted()) {
            try {
                return mockMvc.perform(asyncDispatch(mvc)).andReturn().getResponse().getContentAsString();
            } catch (Exception ignored) {
                return mvc.getResponse().getContentAsString();
            }
        }
        return mvc.getResponse().getContentAsString();
    }

    private static int countOccurrences(String haystack, String needle) {
        int count = 0;
        int idx = 0;
        while ((idx = haystack.indexOf(needle, idx)) != -1) {
            count++;
            idx += needle.length();
        }
        return count;
    }

    private static <T> void assertThat(T actual, org.hamcrest.Matcher<? super T> matcher) {
        org.hamcrest.MatcherAssert.assertThat(actual, matcher);
    }
}
