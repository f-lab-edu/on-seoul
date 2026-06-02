package dev.jazzybyte.onseoul.chat.adapter.out.agent;

import dev.jazzybyte.onseoul.chat.domain.ChatTurn;
import dev.jazzybyte.onseoul.chat.port.out.AiServiceStreamPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

import java.time.Duration;
import java.util.List;
import java.util.concurrent.TimeoutException;

@Slf4j
@Component
public class ChatAgentClient implements AiServiceStreamPort {

    private final WebClient webClient;
    private final AiServiceProperties properties;

    public ChatAgentClient(@Qualifier("aiServiceWebClient") final WebClient webClient,
                           final AiServiceProperties properties) {
        this.webClient = webClient;
        this.properties = properties;
    }

    @Override
    public Flux<String> stream(String question, long roomId, long messageId, Double lat, Double lng,
                               List<ChatTurn> history) {
        List<AiChatRequest.Turn> turns = (history == null ? List.<ChatTurn>of() : history).stream()
                .map(t -> new AiChatRequest.Turn(t.role(), t.content()))
                .toList();
        AiChatRequest body = new AiChatRequest(roomId, messageId, question, lat, lng, turns);
        log.info("[Chat] 스트림 요청 to AI 서비스 - ({})", body);

        return webClient.post()
                .uri("/chat/stream")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(body)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {})
                .timeout(Duration.ofSeconds(properties.streamTimeoutSeconds()))
                .mapNotNull(ServerSentEvent::data)
                .onErrorMap(TimeoutException.class,
                        e -> new OnSeoulApiException(ErrorCode.AI_SERVICE_TIMEOUT,
                                "AI 서비스 스트림 타임아웃: " + properties.streamTimeoutSeconds() + "초 초과", e))
                .onErrorMap(e -> !(e instanceof OnSeoulApiException),
                        e -> new OnSeoulApiException(ErrorCode.AI_SERVICE_ERROR,
                                "AI 서비스 스트림 오류: " + e.getMessage(), e));
    }
}
