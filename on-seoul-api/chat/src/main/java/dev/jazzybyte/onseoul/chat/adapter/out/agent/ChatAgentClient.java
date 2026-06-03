package dev.jazzybyte.onseoul.chat.adapter.out.agent;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.chat.domain.ChatTurn;
import dev.jazzybyte.onseoul.chat.port.out.AiServiceStreamPort;
import dev.jazzybyte.onseoul.chat.port.out.AiStreamEvent;
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

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private final WebClient webClient;
    private final AiServiceProperties properties;

    public ChatAgentClient(@Qualifier("aiServiceWebClient") final WebClient webClient,
                           final AiServiceProperties properties) {
        this.webClient = webClient;
        this.properties = properties;
    }

    @Override
    public Flux<AiStreamEvent> stream(String question, long roomId, long messageId, Double lat, Double lng,
                                      List<ChatTurn> history) {
        List<AiChatRequest.Turn> turns = (history == null ? List.<ChatTurn>of() : history).stream()
                .map(t -> new AiChatRequest.Turn(t.role(), t.content()))
                .toList();
        AiChatRequest body = new AiChatRequest(roomId, messageId, question, lat, lng, turns);
        // PII 보호: 질문/대화 content 평문은 로깅하지 않고 식별자와 history 건수만 INFO로 남긴다.
        log.info("[Chat] 스트림 요청 to AI 서비스 - roomId={}, messageId={}, historySize={}",
                roomId, messageId, turns.size());

        return webClient.post()
                .uri("/chat/stream")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(body)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {})
                .timeout(Duration.ofSeconds(properties.streamTimeoutSeconds()))
                .mapNotNull(this::toStreamEvent)
                .onErrorMap(TimeoutException.class,
                        e -> new OnSeoulApiException(ErrorCode.AI_SERVICE_TIMEOUT,
                                "AI 서비스 스트림 타임아웃: " + properties.streamTimeoutSeconds() + "초 초과", e))
                .onErrorMap(e -> !(e instanceof OnSeoulApiException),
                        e -> new OnSeoulApiException(ErrorCode.AI_SERVICE_ERROR,
                                "AI 서비스 스트림 오류: " + e.getMessage(), e));
    }

    /**
     * SSE data를 {@link AiStreamEvent}로 변환한다. data가 없는 keep-alive 프레임은 null로 걸러진다.
     *
     * <p>final 식별: data가 JSON 객체이고 {@code answer} 키를 가지며 {@code error} 키가 없을 때.
     * AI 서비스의 {@code workflow_error}/{@code error} 이벤트도 answer를 담을 수 있으나 {@code error}
     * 키가 함께 있으므로 final로 저장하지 않는다(이력에는 정상 답변만 남긴다). 원본 data는 어떤
     * 이벤트든 그대로 프론트로 relay된다.
     */
    private AiStreamEvent toStreamEvent(ServerSentEvent<String> sse) {
        String data = sse.data();
        if (data == null) {
            return null;
        }
        try {
            JsonNode node = OBJECT_MAPPER.readTree(data);
            if (node.isObject() && node.has("answer") && !node.has("error")) {
                JsonNode answer = node.get("answer");
                return AiStreamEvent.finalEvent(data, answer.isNull() ? "" : answer.asText());
            }
        } catch (Exception e) {
            // JSON이 아니거나 파싱 실패 — relay 전용 이벤트로 취급(프론트 스트림에는 영향 없음).
            log.debug("[Chat] SSE data를 JSON으로 파싱하지 못해 relay 전용으로 처리합니다.");
        }
        return AiStreamEvent.relay(data);
    }
}
