package dev.jazzybyte.onseoul.chat.adapter.in.web;

import dev.jazzybyte.onseoul.chat.port.in.QueryAndStreamUseCase;
import dev.jazzybyte.onseoul.chat.port.in.QueryAndStreamUseCase.StreamResult;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryCommand;
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

    private static final String GENERIC_ERROR_MESSAGE =
            "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.";

    private final QueryAndStreamUseCase queryAndStreamUseCase;

    public ChatController(final QueryAndStreamUseCase queryAndStreamUseCase) {
        this.queryAndStreamUseCase = queryAndStreamUseCase;
    }

    @PostMapping(value = "/api/chat/query", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter query(
            @RequestAttribute Long userId,
            @Valid @RequestBody QueryRequest request) {

        SseEmitter emitter = new SseEmitter(120_000L);

        // prepare(@Transactional)는 streamAndSave 진입 시 동기 실행된다.
        // 실패(CHAT_ROOM_NOT_FOUND 등)하면 여기서 예외가 던져지므로 init은 emit되지 않고 error 경로로 간다.
        final StreamResult result;
        try {
            result = queryAndStreamUseCase.streamAndSave(
                    new SendQueryCommand(userId, request.roomId(), request.question(),
                            request.lat(), request.lng()));
        } catch (Exception e) {
            sendErrorEvent(emitter, e);
            emitter.complete();
            return emitter;
        }

        // init은 항상 첫 이벤트 — AI 스트림 토큰보다 먼저 1회 emit한다.
        try {
            emitter.send(SseEmitter.event()
                    .name("init")
                    .data(new InitEvent(result.roomId(), result.created()), MediaType.APPLICATION_JSON));
        } catch (IOException e) {
            emitter.completeWithError(e);
            return emitter;
        }

        Disposable subscription = result.tokens().subscribe(
                token -> {
                    try {
                        emitter.send(SseEmitter.event().data(token));
                    } catch (IOException e) {
                        emitter.completeWithError(e);
                    }
                },
                error -> {
                    sendErrorEvent(emitter, error);
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

    private void sendErrorEvent(SseEmitter emitter, Throwable error) {
        try {
            String clientMessage = (error instanceof OnSeoulApiException)
                    ? error.getMessage() : GENERIC_ERROR_MESSAGE;
            emitter.send(SseEmitter.event()
                    .name("error")
                    .data(clientMessage));
        } catch (IOException ignored) {
        }
    }
}
