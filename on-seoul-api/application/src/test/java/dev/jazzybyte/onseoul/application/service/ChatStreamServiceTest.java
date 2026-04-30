package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryUseCase;
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
        SendQueryCommand command = new SendQueryCommand(1L, null, "서울 문화행사 알려줘");
        when(sendQueryUseCase.prepare(command)).thenReturn(10L);
        when(aiServiceStreamPort.stream("서울 문화행사 알려줘", 10L))
                .thenReturn(Flux.just("안녕", "하세요", "!"));

        StepVerifier.create(service.streamAndSave(command))
                .expectNext("안녕")
                .expectNext("하세요")
                .expectNext("!")
                .verifyComplete();
    }

    @Test
    @DisplayName("streamAndSave() — 스트림 완료 시 청크를 이어붙인 전체 답변으로 saveAnswer가 호출된다")
    void streamAndSave_savesFullAnswerOnComplete() throws InterruptedException {
        SendQueryCommand command = new SendQueryCommand(1L, 5L, "오늘 날씨는?");
        when(sendQueryUseCase.prepare(command)).thenReturn(5L);
        when(aiServiceStreamPort.stream("오늘 날씨는?", 5L))
                .thenReturn(Flux.just("맑", "음", "입니다"));

        StepVerifier.create(service.streamAndSave(command))
                .expectNext("맑")
                .expectNext("음")
                .expectNext("입니다")
                .verifyComplete();

        // doOnComplete는 boundedElastic 스레드에서 실행되므로 완료 신호 전파 후 저장까지 짧은 대기
        Thread.sleep(100);

        verify(sendQueryUseCase).saveAnswer(5L, "맑음입니다");
    }

    @Test
    @DisplayName("streamAndSave() — prepare(command)가 올바른 command로 호출되고 반환된 roomId가 stream()에 전달된다")
    void streamAndSave_prepare_calledWithCommand() {
        SendQueryCommand command = new SendQueryCommand(2L, null, "체육시설 예약 방법");
        when(sendQueryUseCase.prepare(command)).thenReturn(99L);
        when(aiServiceStreamPort.stream("체육시설 예약 방법", 99L))
                .thenReturn(Flux.just("안내드리겠습니다"));

        StepVerifier.create(service.streamAndSave(command))
                .expectNext("안내드리겠습니다")
                .verifyComplete();

        verify(sendQueryUseCase).prepare(command);
        verify(aiServiceStreamPort).stream("체육시설 예약 방법", 99L);
    }

    @Test
    @DisplayName("streamAndSave() — saveAnswer에서 예외 발생 시 Flux가 정상 complete된다 (onError로 전파되지 않는다)")
    void streamAndSave_saveAnswerFails_streamStillCompletes() throws InterruptedException {
        SendQueryCommand command = new SendQueryCommand(1L, 7L, "진료 예약 안내");
        when(sendQueryUseCase.prepare(command)).thenReturn(7L);
        when(aiServiceStreamPort.stream("진료 예약 안내", 7L))
                .thenReturn(Flux.just("진료", "안내"));
        doThrow(new RuntimeException("DB 저장 실패"))
                .when(sendQueryUseCase).saveAnswer(anyLong(), anyString());

        StepVerifier.create(service.streamAndSave(command))
                .expectNext("진료")
                .expectNext("안내")
                .verifyComplete();

        Thread.sleep(100);

        verify(sendQueryUseCase).saveAnswer(7L, "진료안내");
    }

    @Test
    @DisplayName("streamAndSave() — 빈 스트림일 때 saveAnswer(\"\")가 호출된다")
    void streamAndSave_emptyStream_saveAnswerCalledWithEmptyString() throws InterruptedException {
        SendQueryCommand command = new SendQueryCommand(1L, 3L, "존재하지 않는 서비스");
        when(sendQueryUseCase.prepare(command)).thenReturn(3L);
        when(aiServiceStreamPort.stream("존재하지 않는 서비스", 3L))
                .thenReturn(Flux.empty());

        StepVerifier.create(service.streamAndSave(command))
                .verifyComplete();

        Thread.sleep(100);

        verify(sendQueryUseCase).saveAnswer(3L, "");
    }
}
