package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.port.in.QueryAndStreamUseCase;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase.PrepareResult;
import dev.jazzybyte.onseoul.chat.port.out.AiServiceStreamPort;
import dev.jazzybyte.onseoul.chat.port.out.AiStreamEvent;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.scheduler.Schedulers;

import java.util.concurrent.atomic.AtomicReference;

@Slf4j
@Service
@RequiredArgsConstructor
public class ChatStreamService implements QueryAndStreamUseCase {

    private final SendQueryUseCase sendQueryUseCase;
    private final AiServiceStreamPort aiServiceStreamPort;

    @Override
    public StreamResult streamAndSave(SendQueryCommand command) {
        // prepare는 @Transactional이며 여기서 동기 실행되므로 roomId/created를 토큰 구독 전에 확보한다.
        PrepareResult prepared = sendQueryUseCase.prepare(command);

        // final 이벤트에서 추출한 answer를 보관한다(스트림은 단일 스레드 직렬 실행 — 아래 publishOn 참조).
        // final이 한 번도 오지 않으면 null로 남고, 그 경우 빈 문자열로 저장한다(기존 saveAnswer 계약 유지).
        AtomicReference<String> finalAnswer = new AtomicReference<>(null);

        Flux<String> tokens = aiServiceStreamPort.stream(
                        command.question(), prepared.roomId(), prepared.messageId(),
                        command.lat(), command.lng(), prepared.history())
                .publishOn(Schedulers.boundedElastic())  // Netty 이벤트 루프 → boundedElastic 전환(직렬 실행 보장)
                .doOnNext(event -> {
                    // step/final 구분 없이 원본 data는 프론트로 그대로 relay된다(아래 map).
                    // 저장 대상은 final 이벤트의 answer뿐 — step JSON을 concat하지 않는다.
                    if (event.isFinal()) {
                        finalAnswer.set(event.finalAnswer());
                    }
                })
                .map(AiStreamEvent::raw)   // 프론트로 흘리는 스트림은 원본 그대로 (변경 금지)
                .doOnComplete(() -> {
                    try {
                        String answer = finalAnswer.get();
                        // final 미수신(예: 카드만 있는 비정상 종료) 시 빈 문자열 — streamAndSave_emptyStream 계약과 일관.
                        sendQueryUseCase.saveAnswer(prepared.roomId(), answer == null ? "" : answer);
                    } catch (Exception e) {
                        // 저장 실패 시에도 onComplete는 그대로 전파 — 클라이언트 정상 종료 보장
                        log.error("ASSISTANT 응답 저장 실패: roomId={}", prepared.roomId(), e);
                    }
                });

        return new StreamResult(prepared.roomId(), prepared.created(), tokens);
    }
}
