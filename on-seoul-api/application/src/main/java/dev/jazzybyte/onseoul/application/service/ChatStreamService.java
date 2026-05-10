package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.port.in.QueryAndStreamUseCase;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryUseCase;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryUseCase.PrepareResult;
import dev.jazzybyte.onseoul.domain.port.out.AiServiceStreamPort;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.scheduler.Schedulers;

@Slf4j
@Service
@RequiredArgsConstructor
public class ChatStreamService implements QueryAndStreamUseCase {

    private final SendQueryUseCase sendQueryUseCase;
    private final AiServiceStreamPort aiServiceStreamPort;

    @Override
    public Flux<String> streamAndSave(SendQueryCommand command) {
        PrepareResult prepared = sendQueryUseCase.prepare(command);
        StringBuilder buffer = new StringBuilder();

        return aiServiceStreamPort.stream(
                        command.question(), prepared.roomId(), prepared.messageId(),
                        command.lat(), command.lng())
                .publishOn(Schedulers.boundedElastic())  // Netty 이벤트 루프 → boundedElastic 전환(블로킹 작업 허용 및 직렬 실행 보장)
                .doOnNext(buffer::append)                // 단일 스레드 직렬 실행 → StringBuilder 안전
                .doOnComplete(() -> {
                    try {
                        sendQueryUseCase.saveAnswer(prepared.roomId(), buffer.toString());
                    } catch (Exception e) {
                        // 저장 실패 시 스트림 완료(onComplete)는 그대로 전파 — 클라이언트 정상 종료 보장
                        log.error("ASSISTANT 응답 저장 실패: roomId={}", prepared.roomId(), e);
                    }
                });
    }
}
