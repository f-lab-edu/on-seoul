package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.ChatTurn;
import dev.jazzybyte.onseoul.chat.port.in.QueryAndStreamUseCase.StreamResult;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase.PrepareResult;
import dev.jazzybyte.onseoul.chat.port.out.AiServiceStreamPort;
import dev.jazzybyte.onseoul.chat.port.out.AiStreamEvent;
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

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class ChatStreamServiceTest {

    @Mock private SendQueryUseCase sendQueryUseCase;
    @Mock private AiServiceStreamPort aiServiceStreamPort;

    private ChatStreamService service;

    @BeforeEach
    void setUp() {
        service = new ChatStreamService(sendQueryUseCase, aiServiceStreamPort);
    }

    @Test
    @DisplayName("streamAndSave() — 모든 이벤트의 원본 data가 토큰 Flux로 그대로 relay된다")
    void streamAndSave_relaysAllRawData() {
        SendQueryCommand command = new SendQueryCommand(1L, null, "서울 문화행사 알려줘", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(10L, 1L, true, List.of()));
        when(aiServiceStreamPort.stream("서울 문화행사 알려줘", 10L, 1L, null, null, List.of()))
                .thenReturn(Flux.just(
                        AiStreamEvent.relay("{\"stage\":\"routing\"}"),
                        AiStreamEvent.finalEvent("{\"message_id\":84,\"answer\":\"안녕하세요\"}", "안녕하세요")));

        StreamResult result = service.streamAndSave(command);

        StepVerifier.create(result.tokens())
                .expectNext("{\"stage\":\"routing\"}")
                .expectNext("{\"message_id\":84,\"answer\":\"안녕하세요\"}")
                .verifyComplete();
    }

    @Test
    @DisplayName("streamAndSave() — 신규 방이면 created=true, roomId가 StreamResult에 담긴다")
    void streamAndSave_newRoom_createdTrue() {
        SendQueryCommand command = new SendQueryCommand(1L, null, "새 질문", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(42L, 1L, true, List.of()));
        when(aiServiceStreamPort.stream(any(), anyLong(), anyLong(), any(), any(), any()))
                .thenReturn(Flux.empty());

        StreamResult result = service.streamAndSave(command);

        assertThat(result.roomId()).isEqualTo(42L);
        assertThat(result.created()).isTrue();
    }

    @Test
    @DisplayName("streamAndSave() — 기존 방이면 created=false")
    void streamAndSave_existingRoom_createdFalse() {
        SendQueryCommand command = new SendQueryCommand(1L, 7L, "이어 질문", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(7L, 2L, false, List.of()));
        when(aiServiceStreamPort.stream(any(), anyLong(), anyLong(), any(), any(), any()))
                .thenReturn(Flux.empty());

        StreamResult result = service.streamAndSave(command);

        assertThat(result.roomId()).isEqualTo(7L);
        assertThat(result.created()).isFalse();
    }

    @Test
    @DisplayName("streamAndSave() — final 이벤트의 answer만 저장되고 step JSON은 concat되지 않는다")
    void streamAndSave_savesOnlyFinalAnswer() {
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "오늘 날씨는?", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L, false, List.of()));
        when(aiServiceStreamPort.stream("오늘 날씨는?", 5L, 2L, null, null, List.of()))
                .thenReturn(Flux.just(
                        AiStreamEvent.relay("{\"stage\":\"routing\"}"),
                        AiStreamEvent.relay("{\"stage\":\"answering\"}"),
                        AiStreamEvent.finalEvent("{\"answer\":\"맑음입니다\"}", "맑음입니다")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(3)
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase).saveAnswer(5L, "맑음입니다");
    }

    @Test
    @DisplayName("streamAndSave() — prepare가 올바른 command로 호출되고 roomId/messageId/history가 stream에 전달된다")
    void streamAndSave_prepare_calledWithCommand() {
        SendQueryCommand command = new SendQueryCommand(2L, null, "체육시설 예약 방법", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(99L, 3L, true, List.of()));
        when(aiServiceStreamPort.stream("체육시설 예약 방법", 99L, 3L, null, null, List.of()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"안내드리겠습니다\"}", "안내드리겠습니다")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .verifyComplete();

        verify(sendQueryUseCase).prepare(command);
        verify(aiServiceStreamPort).stream("체육시설 예약 방법", 99L, 3L, null, null, List.of());
    }

    @Test
    @DisplayName("streamAndSave() — saveAnswer 예외 발생 시 토큰 Flux는 정상 complete된다")
    void streamAndSave_saveAnswerFails_streamStillCompletes() {
        SendQueryCommand command = new SendQueryCommand(1L, 7L, "진료 예약 안내", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(7L, 4L, false, List.of()));
        when(aiServiceStreamPort.stream("진료 예약 안내", 7L, 4L, null, null, List.of()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"진료안내\"}", "진료안내")));
        doThrow(new RuntimeException("DB 저장 실패"))
                .when(sendQueryUseCase).saveAnswer(anyLong(), anyString());

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase).saveAnswer(7L, "진료안내");
    }

    @Test
    @DisplayName("streamAndSave() — final 미수신(빈 스트림)이면 saveAnswer(\"\")가 호출된다")
    void streamAndSave_noFinal_saveAnswerCalledWithEmptyString() {
        SendQueryCommand command = new SendQueryCommand(1L, 3L, "존재하지 않는 서비스", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(3L, 5L, false, List.of()));
        when(aiServiceStreamPort.stream("존재하지 않는 서비스", 3L, 5L, null, null, List.of()))
                .thenReturn(Flux.empty());

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase).saveAnswer(3L, "");
    }

    @Test
    @DisplayName("streamAndSave() — final.answer가 빈 문자열이면 빈 문자열이 저장된다(카드만 있는 MAP 케이스)")
    void streamAndSave_finalWithEmptyAnswer_savesEmptyString() {
        SendQueryCommand command = new SendQueryCommand(1L, 3L, "근처 시설 지도", 37.5, 127.0);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(3L, 5L, false, List.of()));
        when(aiServiceStreamPort.stream("근처 시설 지도", 3L, 5L, 37.5, 127.0, List.of()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"\",\"intent\":\"MAP\"}", "")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase).saveAnswer(3L, "");
    }

    @Test
    @DisplayName("streamAndSave() — workflow_error(relay 전용, final 부재) 스트림은 폴백 텍스트를 저장하지 않고 saveAnswer(\"\")를 호출한다(QA 보강)")
    void streamAndSave_workflowErrorRelayOnly_savesEmptyStringNotFallback() {
        SendQueryCommand command = new SendQueryCommand(1L, 3L, "오류 유발 질문", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(3L, 5L, false, List.of()));
        // 어댑터는 workflow_error(answer+error 동반)를 relay 전용으로 변환한다 → final 부재.
        when(aiServiceStreamPort.stream("오류 유발 질문", 3L, 5L, null, null, List.of()))
                .thenReturn(Flux.just(
                        AiStreamEvent.relay("{\"stage\":\"routing\"}"),
                        AiStreamEvent.relay("{\"answer\":\"폴백 답변\",\"error\":\"처리 중 오류\"}")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                // 원본 data는 프론트로 그대로 relay된다(에러 텍스트 포함).
                .expectNext("{\"stage\":\"routing\"}")
                .expectNext("{\"answer\":\"폴백 답변\",\"error\":\"처리 중 오류\"}")
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        // 이력에는 폴백/에러 텍스트가 남지 않는다 — 빈 문자열만 저장.
        verify(sendQueryUseCase).saveAnswer(3L, "");
        verify(sendQueryUseCase, never()).saveAnswer(eq(3L), eq("폴백 답변"));
    }

    @Test
    @DisplayName("streamAndSave() — final.answer가 null이면 빈 문자열이 저장된다(QA 보강)")
    void streamAndSave_finalNullAnswer_savesEmptyString() {
        SendQueryCommand command = new SendQueryCommand(1L, 3L, "지도만", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(3L, 5L, false, List.of()));
        // 어댑터 계약상 answer=null은 finalEvent에서 빈 문자열로 정규화된다.
        when(aiServiceStreamPort.stream("지도만", 3L, 5L, null, null, List.of()))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":null,\"intent\":\"MAP\"}", null)));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase).saveAnswer(3L, "");
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
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 7L, false, history));
        when(aiServiceStreamPort.stream("그 중 무료인 것만", 5L, 7L, null, null, history))
                .thenReturn(Flux.just(AiStreamEvent.finalEvent("{\"answer\":\"무료 행사 안내\"}", "무료 행사 안내")));

        StepVerifier.create(service.streamAndSave(command).tokens())
                .expectNextCount(1)
                .verifyComplete();

        verify(aiServiceStreamPort).stream("그 중 무료인 것만", 5L, 7L, null, null, history);
    }
}
