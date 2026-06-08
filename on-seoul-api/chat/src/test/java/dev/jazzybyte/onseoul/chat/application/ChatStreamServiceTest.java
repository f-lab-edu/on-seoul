package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.Carryover;
import dev.jazzybyte.onseoul.chat.domain.ChatTurn;
import dev.jazzybyte.onseoul.chat.port.in.QueryAndStreamUseCase.StreamResult;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase.PrepareResult;
import dev.jazzybyte.onseoul.chat.port.out.AiServiceStreamPort;
import dev.jazzybyte.onseoul.chat.port.out.AiStreamEvent;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import reactor.core.publisher.Flux;
import reactor.test.StepVerifier;

import java.time.Duration;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class ChatStreamServiceTest {

    @Mock private SendQueryUseCase sendQueryUseCase;
    @Mock private AiServiceStreamPort aiServiceStreamPort;

    private ChatStreamService service;
    private ChatConcurrencyGuard guard;

    @BeforeEach
    void setUp() {
        guard = new ChatConcurrencyGuard(new ChatConcurrencyProperties(2, 50, 5));
        service = new ChatStreamService(sendQueryUseCase, aiServiceStreamPort, guard);
    }

    @Test
    @DisplayName("streamAndSave() — 모든 이벤트의 원본 data가 토큰 Flux로 그대로 relay된다")
    void streamAndSave_relaysAllRawData() {
        SendQueryCommand command = new SendQueryCommand(1L, null, "서울 문화행사 알려줘", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(10L, 1L, true, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("서울 문화행사 알려줘", 10L, 1L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(
                        AiStreamEvent.relay("{\"step\":\"routing\"}"),
                        AiStreamEvent.finalEvent("{\"message_id\":84,\"answer\":\"안녕하세요\"}", "안녕하세요")));

        StreamResult result = service.streamAndSave(command);

        StepVerifier.create(result.tokens())
                .expectNext("{\"step\":\"routing\"}")
                .expectNext("{\"message_id\":84,\"answer\":\"안녕하세요\"}")
                .verifyComplete();
    }

    @Test
    @DisplayName("streamAndSave() — progress step=re_searching relay 이벤트가 토큰 Flux로 원본 그대로 전달된다 (재시도 진행 패스스루 회귀)")
    void streamAndSave_reSearchingProgress_relayedRaw() {
        String reSearching = "{\"step\":\"re_searching\",\"message\":\"다른 방식으로 다시 검색하고 있습니다...\"}";
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "강남구 문화행사", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("강남구 문화행사", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(
                        AiStreamEvent.relay(reSearching),
                        AiStreamEvent.finalEvent("{\"answer\":\"강남구 안내\"}", "강남구 안내")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNext(reSearching)
                .expectNext("{\"answer\":\"강남구 안내\"}")
                .verifyComplete();
    }

    @Test
    @DisplayName("streamAndSave() — 재시도 시퀀스(routing→searching→re_searching→searching→answering→final)가 누락·재정렬 없이 동일 순서로 relay된다 (회귀)")
    void streamAndSave_retrySequence_relayedInOrder() {
        String routing = "{\"step\":\"routing\",\"message\":\"질문을 분석하고 있습니다...\"}";
        String searching = "{\"step\":\"searching\",\"message\":\"관련 정보를 검색하고 있습니다...\"}";
        String reSearching = "{\"step\":\"re_searching\",\"message\":\"다른 방식으로 다시 검색하고 있습니다...\"}";
        String answering = "{\"step\":\"answering\",\"message\":\"답변을 생성하고 있습니다...\"}";
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "강남구 문화행사", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("강남구 문화행사", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(
                        AiStreamEvent.relay(routing),
                        AiStreamEvent.relay(searching),
                        AiStreamEvent.relay(reSearching),
                        AiStreamEvent.relay(searching),
                        AiStreamEvent.relay(answering),
                        AiStreamEvent.finalEvent("{\"answer\":\"강남구 안내\"}", "강남구 안내")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNext(routing)
                .expectNext(searching)
                .expectNext(reSearching)
                .expectNext(searching)
                .expectNext(answering)
                .expectNext("{\"answer\":\"강남구 안내\"}")
                .verifyComplete();

        // re_searching은 중간 진행 이벤트이므로 이력에는 final.answer만 저장된다.
        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "강남구 안내", null, (String) null);
    }

    @Test
    @DisplayName("streamAndSave() — 신규 방이면 created=true, roomId가 StreamResult에 담긴다")
    void streamAndSave_newRoom_createdTrue() {
        SendQueryCommand command = new SendQueryCommand(1L, null, "새 질문", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(42L, 1L, true, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream(any(), anyLong(), anyLong(), any(), any(), any(), any()))
                .thenReturn(Flux.empty());

        StreamResult result = service.streamAndSave(command);

        assertThat(result.roomId()).isEqualTo(42L);
        assertThat(result.created()).isTrue();
    }

    @Test
    @DisplayName("streamAndSave() — 기존 방이면 created=false")
    void streamAndSave_existingRoom_createdFalse() {
        SendQueryCommand command = new SendQueryCommand(1L, 7L, "이어 질문", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(7L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream(any(), anyLong(), anyLong(), any(), any(), any(), any()))
                .thenReturn(Flux.empty());

        StreamResult result = service.streamAndSave(command);

        assertThat(result.roomId()).isEqualTo(7L);
        assertThat(result.created()).isFalse();
    }

    @Test
    @DisplayName("streamAndSave() — final 이벤트의 answer만 저장되고 step JSON은 concat되지 않는다")
    void streamAndSave_savesOnlyFinalAnswer() {
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "오늘 날씨는?", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("오늘 날씨는?", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(
                        AiStreamEvent.relay("{\"step\":\"routing\"}"),
                        AiStreamEvent.relay("{\"step\":\"answering\"}"),
                        AiStreamEvent.finalEvent("{\"answer\":\"맑음입니다\"}", "맑음입니다")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(3)
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "맑음입니다", null, (String) null);
    }

    @Test
    @DisplayName("streamAndSave() — final 이벤트의 service_cards가 answer와 함께 saveAnswer로 전달된다")
    void streamAndSave_savesServiceCardsFromFinal() {
        String cardsJson = "[{\"service_id\":\"S1\",\"name\":\"강남 음악회\"}]";
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "강남구 문화행사", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("강남구 문화행사", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(
                        AiStreamEvent.relay("{\"step\":\"routing\"}"),
                        AiStreamEvent.finalEvent("{\"answer\":\"강남구 안내\"}", "강남구 안내", cardsJson)));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(2)
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "강남구 안내", cardsJson, (String) null);
    }

    @Test
    @DisplayName("streamAndSave() — final 미수신이면 answer=\"\" + serviceCards=null로 저장된다")
    void streamAndSave_noFinal_savesEmptyAnswerNullCards() {
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "질문", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("질문", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(AiStreamEvent.relay("{\"step\":\"routing\"}")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "", null, (String) null);
    }

    @Test
    @DisplayName("streamAndSave() — prepare가 올바른 command로 호출되고 roomId/messageId/history가 stream에 전달된다")
    void streamAndSave_prepare_calledWithCommand() {
        SendQueryCommand command = new SendQueryCommand(2L, null, "체육시설 예약 방법", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(99L, 3L, true, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("체육시설 예약 방법", 99L, 3L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"안내드리겠습니다\"}", "안내드리겠습니다")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .verifyComplete();

        verify(sendQueryUseCase).prepare(command);
        verify(aiServiceStreamPort).stream("체육시설 예약 방법", 99L, 3L, null, null, List.of(), Carryover.empty());
    }

    @Test
    @DisplayName("streamAndSave() — saveAnswer 예외 발생 시 토큰 Flux는 정상 complete된다")
    void streamAndSave_saveAnswerFails_streamStillCompletes() {
        SendQueryCommand command = new SendQueryCommand(1L, 7L, "진료 예약 안내", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(7L, 4L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("진료 예약 안내", 7L, 4L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"진료안내\"}", "진료안내")));
        doThrow(new RuntimeException("DB 저장 실패"))
                .when(sendQueryUseCase).saveAnswer(anyLong(), anyString(), any(), any());

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase, timeout(2000)).saveAnswer(7L, "진료안내", null, (String) null);
    }

    @Test
    @DisplayName("streamAndSave() — final 미수신(빈 스트림)이면 saveAnswer(\"\")가 호출된다")
    void streamAndSave_noFinal_saveAnswerCalledWithEmptyString() {
        SendQueryCommand command = new SendQueryCommand(1L, 3L, "존재하지 않는 서비스", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(3L, 5L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("존재하지 않는 서비스", 3L, 5L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.empty());

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase, timeout(2000)).saveAnswer(3L, "", null, (String) null);
    }

    @Test
    @DisplayName("streamAndSave() — final.answer가 빈 문자열이면 빈 문자열이 저장된다(카드만 있는 MAP 케이스)")
    void streamAndSave_finalWithEmptyAnswer_savesEmptyString() {
        SendQueryCommand command = new SendQueryCommand(1L, 3L, "근처 시설 지도", 37.5, 127.0);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(3L, 5L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("근처 시설 지도", 3L, 5L, 37.5, 127.0, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"\",\"intent\":\"MAP\"}", "")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase, timeout(2000)).saveAnswer(3L, "", null, (String) null);
    }

    @Test
    @DisplayName("streamAndSave() — workflow_error(relay 전용, final 부재) 스트림은 폴백 텍스트를 저장하지 않고 saveAnswer(\"\")를 호출한다(QA 보강)")
    void streamAndSave_workflowErrorRelayOnly_savesEmptyStringNotFallback() {
        SendQueryCommand command = new SendQueryCommand(1L, 3L, "오류 유발 질문", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(3L, 5L, false, List.of(), Carryover.empty()));
        // 어댑터는 workflow_error(answer+error 동반)를 relay 전용으로 변환한다 → final 부재.
        when(aiServiceStreamPort.stream("오류 유발 질문", 3L, 5L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(
                        AiStreamEvent.relay("{\"step\":\"routing\"}"),
                        AiStreamEvent.relay("{\"answer\":\"폴백 답변\",\"error\":\"처리 중 오류\"}")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                // 원본 data는 프론트로 그대로 relay된다(에러 텍스트 포함).
                .expectNext("{\"step\":\"routing\"}")
                .expectNext("{\"answer\":\"폴백 답변\",\"error\":\"처리 중 오류\"}")
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        // 이력에는 폴백/에러 텍스트가 남지 않는다 — 빈 문자열만 저장.
        verify(sendQueryUseCase, timeout(2000)).saveAnswer(3L, "", null, (String) null);
        verify(sendQueryUseCase, never()).saveAnswer(eq(3L), eq("폴백 답변"), any(), any());
    }

    @Test
    @DisplayName("streamAndSave() — final.answer가 null이면 빈 문자열이 저장된다(QA 보강)")
    void streamAndSave_finalNullAnswer_savesEmptyString() {
        SendQueryCommand command = new SendQueryCommand(1L, 3L, "지도만", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(3L, 5L, false, List.of(), Carryover.empty()));
        // 어댑터 계약상 answer=null은 finalEvent에서 빈 문자열로 정규화된다.
        when(aiServiceStreamPort.stream("지도만", 3L, 5L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":null,\"intent\":\"MAP\"}", null)));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase, timeout(2000)).saveAnswer(3L, "", null, (String) null);
    }

    @Test
    @DisplayName("streamAndSave() — prepare가 예외를 던지면 호출자에게 전파된다(Flux.error가 아닌 throw)")
    void streamAndSave_prepareFails_throwsException() {
        SendQueryCommand command = new SendQueryCommand(1L, null, "오류 유발 질문", null, null);
        when(sendQueryUseCase.prepare(command))
                .thenThrow(new RuntimeException("ChatRoom 생성 실패"));

        assertThatThrownBy(() -> service.streamAndSave(command))
                .isInstanceOf(RuntimeException.class);

        verifyNoInteractions(aiServiceStreamPort);
    }

    @Test
    @DisplayName("streamAndSave() — prepare가 반환한 history가 stream으로 그대로 전달된다")
    void streamAndSave_passesHistoryToStream() {
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "그 중 무료인 것만", null, null);
        List<ChatTurn> history = List.of(
                new ChatTurn("user", "강남구 문화행사 알려줘"),
                new ChatTurn("assistant", "강남구 문화행사 5건을 안내합니다."));
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 7L, false, history, Carryover.empty()));
        when(aiServiceStreamPort.stream("그 중 무료인 것만", 5L, 7L, null, null, history, Carryover.empty()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"무료 행사 안내\"}", "무료 행사 안내")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .verifyComplete();

        verify(aiServiceStreamPort).stream("그 중 무료인 것만", 5L, 7L, null, null, history, Carryover.empty());
    }

    @Test
    @DisplayName("streamAndSave() — final 이벤트의 intent가 캡처되어 answer/serviceCards와 함께 saveAnswer로 전달된다(carryover 영속 회귀)")
    void streamAndSave_capturesFinalIntent_passesToSaveAnswer() {
        String cardsJson = "[{\"service_id\":\"S1\",\"service_name\":\"강남 음악회\"}]";
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "강남구 문화행사", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("강남구 문화행사", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(
                        AiStreamEvent.relay("{\"step\":\"routing\"}"),
                        AiStreamEvent.finalEvent("{\"answer\":\"강남구 안내\"}", "강남구 안내", cardsJson, "SQL_SEARCH")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(2)
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        // intent가 null이 아닌 실제 값으로 끝까지 전달되는지 검증(다음 턴 prev_intent로 영속).
        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "강남구 안내", cardsJson, "SQL_SEARCH");
    }

    @Test
    @DisplayName("streamAndSave() — final 이벤트에 intent가 없으면 saveAnswer로 null intent가 전달된다")
    void streamAndSave_finalWithoutIntent_passesNullIntent() {
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "질문", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("질문", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"답\"}", "답")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .verifyComplete();

        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "답", null, (String) null);
    }

    @Test
    @DisplayName("streamAndSave() — prepare가 반환한 non-empty carryover가 stream으로 그대로 전달된다(carryover 플러밍 회귀)")
    void streamAndSave_passesCarryoverToStream() {
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "그 중 첫 번째", null, null);
        Carryover carryover = new Carryover(List.of(
                new dev.jazzybyte.onseoul.chat.domain.PrevEntity("S1", "강남 음악회"),
                new dev.jazzybyte.onseoul.chat.domain.PrevEntity("S2", "미술 전시")),
                "SQL_SEARCH", null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 7L, false, List.of(), carryover));
        when(aiServiceStreamPort.stream("그 중 첫 번째", 5L, 7L, null, null, List.of(), carryover))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"안내\"}", "안내")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .verifyComplete();

        // prepare가 조립한 carryover가 변형 없이 stream 인자로 흘러가야 한다.
        verify(aiServiceStreamPort).stream("그 중 첫 번째", 5L, 7L, null, null, List.of(), carryover);
    }

    @Test
    @DisplayName("detach — 클라 끊김(relay 미구독)에도 final intent가 doFinally 저장 경로로 보존된다(disconnect 내성 회귀)")
    void streamAndSave_relayNeverSubscribed_intentStillSaved() {
        String cardsJson = "[{\"service_id\":\"S1\",\"service_name\":\"행사\"}]";
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "질문", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("질문", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"답\"}", "답", cardsJson, "VECTOR_SEARCH")));

        // relay(tokens) 미구독 = 클라 즉시 끊김. 저장 구독은 살아서 intent까지 저장해야 한다.
        service.streamAndSave(command);

        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "답", cardsJson, "VECTOR_SEARCH");
    }

    // ── disconnect 내성(detach) ────────────────────────────────────────────

    @Test
    @DisplayName("detach — 클라(relay) 구독이 취소되어도 저장 구독은 살아서 saveAnswer가 호출된다(핵심 유실 버그 해소)")
    void streamAndSave_relayCancelled_saveStillRuns() {
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "오늘 행사", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        // 토큰이 천천히 도착하는 스트림. relay는 첫 토큰 후 취소되지만 저장은 끝까지 가야 한다.
        when(aiServiceStreamPort.stream("오늘 행사", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.concat(
                        Flux.just(AiStreamEvent.relay("{\"step\":\"routing\"}")),
                        Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"행사 안내\"}", "행사 안내"))
                                .delayElements(Duration.ofMillis(100))));

        StreamResult result = service.streamAndSave(command);

        // relay 측은 첫 토큰만 받고 취소(클라 disconnect 시뮬레이션)
        StepVerifier.create(result.tokens().take(1))
                .expectNext("{\"step\":\"routing\"}")
                .verifyComplete();

        // 저장 구독은 별도로 살아 있으므로, final.answer가 결국 저장된다.
        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "행사 안내", null, (String) null);
    }

    @Test
    @DisplayName("detach — 업스트림은 정확히 1회만 구독된다(AI 2회 요청 금지: 저장+relay가 공유)")
    void streamAndSave_upstreamSubscribedOnce() {
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "질문", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));

        AtomicInteger subscribeCount = new AtomicInteger();
        Flux<AiStreamEvent> upstream = Flux.just(
                        AiStreamEvent.finalEvent("{\"answer\":\"답\"}", "답"))
                .doOnSubscribe(s -> subscribeCount.incrementAndGet());
        when(aiServiceStreamPort.stream("질문", 5L, 2L, null, null, List.of(), Carryover.empty())).thenReturn(upstream);

        StreamResult result = service.streamAndSave(command);
        StepVerifier.create(result.tokens()).expectNextCount(1).verifyComplete();

        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "답", null, (String) null);
        // 저장 구독 + relay 구독이 하나의 업스트림을 공유 → 1회 구독
        assertThat(subscribeCount.get()).isEqualTo(1);
    }

    @Test
    @DisplayName("detach — relay를 한 번도 구독하지 않아도(즉시 disconnect) 저장은 수행된다")
    void streamAndSave_relayNeverSubscribed_saveStillRuns() {
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "질문", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("질문", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"답\"}", "답")));

        // result.tokens()를 구독하지 않는다(클라가 즉시 끊긴 상황)
        service.streamAndSave(command);

        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "답", null, (String) null);
    }

    // ── 가드: 동시성 cap ───────────────────────────────────────────────────

    @Test
    @DisplayName("cap — per-user cap 초과 시 CHAT_CONCURRENCY_LIMIT(429)를 던지고 prepare/stream을 호출하지 않는다")
    void streamAndSave_perUserCapExceeded_throws429() {
        // perUser=2 → 같은 사용자의 미완료 스트림 2개를 점유시킨 뒤 3번째에서 거부 확인.
        // never-complete 업스트림으로 permit을 잡아둔다.
        SendQueryCommand cmd = new SendQueryCommand(7L, 5L, "질문", null, null);
        when(sendQueryUseCase.prepare(cmd)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("질문", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.never());

        service.streamAndSave(cmd);
        service.streamAndSave(cmd);

        assertThatThrownBy(() -> service.streamAndSave(cmd))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.CHAT_CONCURRENCY_LIMIT));
    }

    @Test
    @DisplayName("cap — 스트림이 정상 완료되면 permit이 해제되어 다시 생성할 수 있다(누수 없음)")
    void streamAndSave_completes_releasesPermit() {
        ChatConcurrencyGuard tight = new ChatConcurrencyGuard(new ChatConcurrencyProperties(1, 50, 5));
        ChatStreamService svc = new ChatStreamService(sendQueryUseCase, aiServiceStreamPort, tight);

        SendQueryCommand cmd = new SendQueryCommand(7L, 5L, "질문", null, null);
        when(sendQueryUseCase.prepare(cmd)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("질문", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"답\"}", "답")));

        StepVerifier.create(svc.streamAndSave(cmd).tokens()).expectNextCount(1).verifyComplete();
        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "답", null, (String) null);

        // 첫 스트림이 완료되어 permit이 해제됐으므로 perUser=1이어도 다시 가능
        StepVerifier.create(svc.streamAndSave(cmd).tokens()).expectNextCount(1).verifyComplete();
    }

    @Test
    @DisplayName("cap — 업스트림 에러로 끝나도 permit이 해제된다(누수 없음)")
    void streamAndSave_error_releasesPermit() {
        ChatConcurrencyGuard tight = new ChatConcurrencyGuard(new ChatConcurrencyProperties(1, 50, 5));
        ChatStreamService svc = new ChatStreamService(sendQueryUseCase, aiServiceStreamPort, tight);

        SendQueryCommand cmd = new SendQueryCommand(7L, 5L, "질문", null, null);
        when(sendQueryUseCase.prepare(cmd)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("질문", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.error(new RuntimeException("AI 다운")));

        StepVerifier.create(svc.streamAndSave(cmd).tokens())
                .expectError().verify(Duration.ofSeconds(2));

        // 에러 종료 경로에서도 저장은 시도되고(빈 문자열), permit은 해제된다.
        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "", null, (String) null);
        StepVerifier.create(svc.streamAndSave(cmd).tokens())
                .expectError().verify(Duration.ofSeconds(2));
    }

    @Test
    @DisplayName("모든 종료 경로 저장 — 업스트림 에러 시에도 saveAnswer(\"\")가 호출된다")
    void streamAndSave_upstreamError_savesEmptyString() {
        SendQueryCommand cmd = new SendQueryCommand(1L, 5L, "질문", null, null);
        when(sendQueryUseCase.prepare(cmd)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("질문", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.concat(
                        Flux.just(AiStreamEvent.relay("{\"step\":\"routing\"}")),
                        Flux.error(new RuntimeException("스트림 중단"))));

        StepVerifier.create(service.streamAndSave(cmd).tokens())
                .expectNext("{\"step\":\"routing\"}")
                .expectError().verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "", null, (String) null);
    }

    // ── QA 보강: 타임아웃 / permit 누수 / 획득 순서 / 멱등 한계 / replay 불변 ──────

    @Test
    @DisplayName("타임아웃 — 백그라운드 timeout 발화 시 relay에 에러가 전파되고 저장은 \"\"로 수행된다(QA 보강)")
    void streamAndSave_backgroundTimeout_relayErrorsAndSavesEmptyString() {
        // backgroundTimeout=1s. final이 그보다 늦게(2s) 오므로 timeout이 먼저 발화한다.
        ChatConcurrencyGuard timed = new ChatConcurrencyGuard(new ChatConcurrencyProperties(2, 50, 1));
        ChatStreamService svc = new ChatStreamService(sendQueryUseCase, aiServiceStreamPort, timed);

        SendQueryCommand cmd = new SendQueryCommand(1L, 5L, "느린 질문", null, null);
        when(sendQueryUseCase.prepare(cmd)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("느린 질문", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.concat(
                        Flux.just(AiStreamEvent.relay("{\"step\":\"routing\"}")),
                        Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"늦은 답\"}", "늦은 답"))
                                .delayElements(Duration.ofSeconds(2))));

        StepVerifier.create(svc.streamAndSave(cmd).tokens())
                .expectNext("{\"step\":\"routing\"}")
                // 백그라운드 timeout → relaySink.tryEmitError(TimeoutException) → 클라에 에러 전파
                .expectError(java.util.concurrent.TimeoutException.class)
                .verify(Duration.ofSeconds(3));

        // 타임아웃 종료 경로에서도 doFinally가 저장을 보장한다(final 미수신 → "").
        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "", null, (String) null);
    }

    @Test
    @DisplayName("타임아웃 — timeout 후 permit이 해제되어 cap이 막혔던 사용자가 다시 생성할 수 있다(누수 없음, QA 보강)")
    void streamAndSave_timeout_releasesPermit() {
        ChatConcurrencyGuard tight = new ChatConcurrencyGuard(new ChatConcurrencyProperties(1, 50, 1));
        ChatStreamService svc = new ChatStreamService(sendQueryUseCase, aiServiceStreamPort, tight);

        SendQueryCommand cmd = new SendQueryCommand(7L, 5L, "느린 질문", null, null);
        when(sendQueryUseCase.prepare(cmd)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        // 영원히 final이 안 오는 스트림 → backgroundTimeout(1s)으로만 종료된다.
        when(aiServiceStreamPort.stream("느린 질문", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.never());

        svc.streamAndSave(cmd); // relay 미구독(클라 즉시 끊김). 저장 구독은 살아서 1s 뒤 timeout.

        // timeout 종료 경로에서 permit이 해제되어야 perUser=1이어도 재획득 가능.
        verify(sendQueryUseCase, timeout(3000)).saveAnswer(5L, "", null, (String) null);
        StepVerifier.create(svc.streamAndSave(cmd).tokens())
                .expectError(java.util.concurrent.TimeoutException.class)
                .verify(Duration.ofSeconds(3));
    }

    @Test
    @DisplayName("permit 누수 — prepare 예외(구독 와이어 전 실패)에서도 permit이 해제되어 재획득 가능하다(QA 보강)")
    void streamAndSave_prepareThrows_releasesPermitNoLeak() {
        ChatConcurrencyGuard tight = new ChatConcurrencyGuard(new ChatConcurrencyProperties(1, 1, 5));
        ChatStreamService svc = new ChatStreamService(sendQueryUseCase, aiServiceStreamPort, tight);

        SendQueryCommand cmd = new SendQueryCommand(7L, null, "질문", null, null);
        when(sendQueryUseCase.prepare(cmd))
                .thenThrow(new RuntimeException("ChatRoom 생성 실패"))   // 1번째: 실패
                .thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty())); // 2번째: 성공
        when(aiServiceStreamPort.stream(any(), anyLong(), anyLong(), any(), any(), any(), any()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"답\"}", "답")));

        // 1번째: prepare가 throw → launch에서 catch → permit.close() → 호출자에 전파
        assertThatThrownBy(() -> svc.streamAndSave(cmd)).isInstanceOf(RuntimeException.class);

        // permit이 누수됐다면 global=1/perUser=1이 막혀 아래 호출이 429로 떨어졌을 것.
        // 정상 해제됐으므로 재획득되어 스트림이 끝까지 흐른다.
        StepVerifier.create(svc.streamAndSave(cmd).tokens()).expectNextCount(1).verifyComplete();
        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "답", null, (String) null);
    }

    @Test
    @DisplayName("획득 순서 — cap 초과로 거부되면 prepare/stream을 전혀 호출하지 않는다(acquire가 prepare/AI보다 먼저, 비용 단락, QA 보강)")
    void streamAndSave_capRejected_doesNotCallPrepareOrStream() {
        ChatConcurrencyGuard full = new ChatConcurrencyGuard(new ChatConcurrencyProperties(1, 50, 5));
        ChatStreamService svc = new ChatStreamService(sendQueryUseCase, aiServiceStreamPort, full);

        SendQueryCommand cmd = new SendQueryCommand(7L, 5L, "질문", null, null);
        when(sendQueryUseCase.prepare(cmd)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("질문", 5L, 2L, null, null, List.of(), Carryover.empty())).thenReturn(Flux.never());

        svc.streamAndSave(cmd); // perUser=1 점유

        // 2번째는 cap 초과로 즉시 429 — prepare/stream을 다시 타면 안 된다(비용 단락).
        assertThatThrownBy(() -> svc.streamAndSave(cmd)).isInstanceOf(OnSeoulApiException.class);

        // prepare/stream은 1번째 호출분 1회씩만.
        verify(sendQueryUseCase, times(1)).prepare(cmd);
        verify(aiServiceStreamPort, times(1)).stream("질문", 5L, 2L, null, null, List.of(), Carryover.empty());
    }

    @Test
    @DisplayName("멱등 한계 문서화 — 동시 double-submit이 둘 다 last=USER를 읽으면 둘 다 저장된다(DB 제약 부재, per-user cap=2가 폭을 제한). QA 보강")
    void streamAndSave_concurrentDoubleSubmit_idempotencyNotEnforcedAtDb() {
        // 멱등 가드는 "직전 메시지가 ASSISTANT면 skip"이지만 read-then-write라 원자적이지 않다.
        // 두 저장 구독이 동시에 ASSISTANT 부재를 관측하면 둘 다 saveAnswer를 호출할 수 있다.
        // 이는 spring-backend가 명시한 수용된 한계이며, per-user cap(기본 2)이 동시 진입 폭을 좁힌다.
        SendQueryCommand cmd = new SendQueryCommand(1L, 5L, "같은 질문", null, null);
        when(sendQueryUseCase.prepare(cmd)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("같은 질문", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"답\"}", "답")));

        // per-user cap=2 → 같은 사용자가 동시에 최대 2건. 그 이상은 429로 막혀 폭이 제한된다.
        StepVerifier.create(service.streamAndSave(cmd).tokens()).expectNextCount(1).verifyComplete();
        StreamResult second = service.streamAndSave(cmd);
        StepVerifier.create(second.tokens()).expectNextCount(1).verifyComplete();

        // 같은 답이 (멱등 DB 제약 부재로) 최대 2회까지 저장 시도될 수 있음을 회귀로 고정.
        // 멱등 강제는 SendQueryService.saveAnswer의 last-message 판정에 위임(원자성은 DB 제약 미도입).
        verify(sendQueryUseCase, timeout(2000).times(2)).saveAnswer(5L, "답", null, (String) null);
    }

    @Test
    @DisplayName("replay 불변 — 저장 구독이 먼저 시작해 토큰이 흘러간 뒤 늦게 relay를 구독해도 처음부터 모든 토큰을 받는다(QA 보강)")
    void streamAndSave_lateRelaySubscriber_receivesAllBufferedTokens() {
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "질문", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of(), Carryover.empty()));
        when(aiServiceStreamPort.stream("질문", 5L, 2L, null, null, List.of(), Carryover.empty()))
                .thenReturn(Flux.just(
                        AiStreamEvent.relay("{\"step\":\"routing\"}"),
                        AiStreamEvent.relay("{\"step\":\"answering\"}"),
                        AiStreamEvent.finalEvent("{\"answer\":\"완성 답\"}", "완성 답")));

        StreamResult result = service.streamAndSave(command);

        // 저장 구독이 완료될 때까지 기다린 뒤(= 토큰이 이미 다 흘러간 뒤) relay를 늦게 구독한다.
        verify(sendQueryUseCase, timeout(2000)).saveAnswer(5L, "완성 답", null, (String) null);

        // replay().all() 버퍼 덕에 늦은 구독자도 처음부터 3개 토큰을 모두 본다(메모리: 답변 1건 분량).
        StepVerifier.create(result.tokens())
                .expectNext("{\"step\":\"routing\"}")
                .expectNext("{\"step\":\"answering\"}")
                .expectNext("{\"answer\":\"완성 답\"}")
                .verifyComplete();
    }
}
