package dev.jazzybyte.onseoul.adapter.in.web;

import dev.jazzybyte.onseoul.domain.port.in.QueryAndStreamUseCase;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;
import reactor.core.Disposable;

import java.io.IOException;

@RestController
public class ChatController {

    private final QueryAndStreamUseCase queryAndStreamUseCase;

    public ChatController(final QueryAndStreamUseCase queryAndStreamUseCase) {
        this.queryAndStreamUseCase = queryAndStreamUseCase;
    }

    @PostMapping(value = "/query", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter query(
            @RequestAttribute Long userId,
            @Valid @RequestBody QueryRequest request) {

        SseEmitter emitter = new SseEmitter(120_000L);

        Disposable subscription = queryAndStreamUseCase.streamAndSave(
                        new SendQueryCommand(userId, request.roomId(), request.question(), request.lat(), request.lng()))
                .subscribe(
                        token -> {
                            try {
                                emitter.send(SseEmitter.event().data(token));
                            } catch (IOException e) {
                                emitter.completeWithError(e);
                            }
                        },
                        error -> {
                            try {
                                String clientMessage = (error instanceof OnSeoulApiException)
                                        ? error.getMessage() : "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.";
                                emitter.send(SseEmitter.event()
                                        .name("error")
                                        .data(clientMessage));
                            } catch (IOException ignored) {
                            }
                            emitter.completeWithError(error);
                        },
                        emitter::complete
                );

        // 타임아웃 시 emitter 정상 종료 + 업스트림 Flux 구독 해제
        emitter.onTimeout(emitter::complete);
        // emitter 완료(정상/에러/타임아웃) 시 업스트림 Flux 구독 해제 — 리소스 누수 방지
        emitter.onCompletion(subscription::dispose);

        return emitter;
    }
}
