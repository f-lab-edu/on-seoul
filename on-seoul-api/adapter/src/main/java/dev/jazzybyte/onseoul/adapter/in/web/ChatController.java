package dev.jazzybyte.onseoul.adapter.in.web;

import dev.jazzybyte.onseoul.adapter.out.aiservice.AiServicePort;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.domain.port.in.SendQueryUseCase;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;

@RestController
public class ChatController {

    private final SendQueryUseCase sendQueryUseCase;
    private final AiServicePort aiServicePort;

    public ChatController(final SendQueryUseCase sendQueryUseCase,
                          final AiServicePort aiServicePort) {
        this.sendQueryUseCase = sendQueryUseCase;
        this.aiServicePort = aiServicePort;
    }

    @PostMapping(value = "/query", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter query(
            @RequestAttribute Long userId,
            @Valid @RequestBody QueryRequest request) {

        SseEmitter emitter = new SseEmitter(120_000L);

        Long roomId = sendQueryUseCase.prepare(
                new SendQueryCommand(userId, request.roomId(), request.question()));

        StringBuilder answer = new StringBuilder();

        aiServicePort.stream(request.question(), roomId)
                .subscribe(
                        token -> {
                            try {
                                answer.append(token);
                                emitter.send(SseEmitter.event().data(token));
                            } catch (IOException e) {
                                emitter.completeWithError(e);
                            }
                        },
                        error -> {
                            try {
                                emitter.send(SseEmitter.event()
                                        .name("error")
                                        .data(error.getMessage()));
                            } catch (IOException ignored) {
                            }
                            emitter.completeWithError(error);
                        },
                        () -> {
                            sendQueryUseCase.saveAnswer(roomId, answer.toString());
                            emitter.complete();
                        }
                );

        return emitter;
    }
}
