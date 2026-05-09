package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryUseCase;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryUseCase.PrepareResult;
import dev.jazzybyte.onseoul.domain.port.out.AiServiceStreamPort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import reactor.core.publisher.Flux;
import reactor.test.StepVerifier;

import java.time.Duration;

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
    @DisplayName("streamAndSave() — AI 응답 청크들이 Flux로 그대로 발행된다")
    void streamAndSave_emitsAllChunks() {
        SendQueryCommand command = new SendQueryCommand(1L, null, "서울 문화행사 알려줘", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(10L, 1L));
        when(aiServiceStreamPort.stream("서울 문화행사 알려줘", 10L, 1L, null, null))
                .thenReturn(Flux.just("안녕", "하세요", "!"));

        StepVerifier.create(service.streamAndSave(command))
                .expectNext("안녕")
                .expectNext("하세요")
                .expectNext("!")
                .verifyComplete();
    }

    @Test
    @DisplayName("streamAndSave() — 스트림 완료 시 청크를 이어붙인 전체 답변으로 saveAnswer가 호출된다")
    void streamAndSave_savesFullAnswerOnComplete() {
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "오늘 날씨는?", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(5L, 2L));
        when(aiServiceStreamPort.stream("오늘 날씨는?", 5L, 2L, null, null))
                .thenReturn(Flux.just("맑", "음", "입니다"));

        StepVerifier.create(service.streamAndSave(command))
                .expectNext("맑")
                .expectNext("음")
                .expectNext("입니다")
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase).saveAnswer(5L, "맑음입니다");
    }

    @Test
    @DisplayName("streamAndSave() — prepare(command)가 올바른 command로 호출되고 반환된 roomId/messageId가 stream()에 전달된다")
    void streamAndSave_prepare_calledWithCommand() {
        SendQueryCommand command = new SendQueryCommand(2L, null, "체육시설 예약 방법", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(99L, 3L));
        when(aiServiceStreamPort.stream("체육시설 예약 방법", 99L, 3L, null, null))
                .thenReturn(Flux.just("안내드리겠습니다"));

        StepVerifier.create(service.streamAndSave(command))
                .expectNext("안내드리겠습니다")
                .verifyComplete();

        verify(sendQueryUseCase).prepare(command);
        verify(aiServiceStreamPort).stream("체육시설 예약 방법", 99L, 3L, null, null);
    }

    @Test
    @DisplayName("streamAndSave() — saveAnswer에서 예외 발생 시 Flux가 정상 complete된다 (onError로 전파되지 않는다)")
    void streamAndSave_saveAnswerFails_streamStillCompletes() {
        SendQueryCommand command = new SendQueryCommand(1L, 7L, "진료 예약 안내", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(7L, 4L));
        when(aiServiceStreamPort.stream("진료 예약 안내", 7L, 4L, null, null))
                .thenReturn(Flux.just("진료", "안내"));
        doThrow(new RuntimeException("DB 저장 실패"))
                .when(sendQueryUseCase).saveAnswer(anyLong(), anyString());

        StepVerifier.create(service.streamAndSave(command))
                .expectNext("진료")
                .expectNext("안내")
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase).saveAnswer(7L, "진료안내");
    }

    @Test
    @DisplayName("streamAndSave() — 빈 스트림일 때 saveAnswer(\"\")가 호출된다")
    void streamAndSave_emptyStream_saveAnswerCalledWithEmptyString() {
        SendQueryCommand command = new SendQueryCommand(1L, 3L, "존재하지 않는 서비스", null, null);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(3L, 5L));
        when(aiServiceStreamPort.stream("존재하지 않는 서비스", 3L, 5L, null, null))
                .thenReturn(Flux.empty());

        StepVerifier.create(service.streamAndSave(command))
                .expectComplete()
                .verify(Duration.ofSeconds(2));

        verify(sendQueryUseCase).saveAnswer(3L, "");
    }

    @Test
    @DisplayName("streamAndSave() — prepare()가 예외를 던지면 호출자에게 예외가 전파된다 (Flux.error()가 아닌 throw)")
    void streamAndSave_prepareFails_throwsException() {
        SendQueryCommand command = new SendQueryCommand(1L, null, "오류 유발 질문", null, null);
        when(sendQueryUseCase.prepare(command))
                .thenThrow(new RuntimeException("ChatRoom 생성 실패"));

        assertThatThrownBy(() -> service.streamAndSave(command))
                .isInstanceOf(RuntimeException.class);

        verifyNoInteractions(aiServiceStreamPort);
    }

    @Test
    @DisplayName("streamAndSave() — lat/lng가 포함된 command에서 위치 정보가 stream()에 그대로 전달된다")
    void streamAndSave_withLatLng_passedToStream() {
        SendQueryCommand command = new SendQueryCommand(1L, 10L, "근처 문화행사 알려줘", 37.5665, 126.9780);
        when(sendQueryUseCase.prepare(command)).thenReturn(new PrepareResult(10L, 6L));
        when(aiServiceStreamPort.stream("근처 문화행사 알려줘", 10L, 6L, 37.5665, 126.9780))
                .thenReturn(Flux.just("근처 행사 안내"));

        StepVerifier.create(service.streamAndSave(command))
                .expectNext("근처 행사 안내")
                .verifyComplete();

        verify(aiServiceStreamPort).stream("근처 문화행사 알려줘", 10L, 6L, 37.5665, 126.9780);
    }
}
