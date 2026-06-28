package dev.jazzybyte.onseoul.chat.adapter.out.agent;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.chat.domain.Carryover;
import dev.jazzybyte.onseoul.chat.domain.ChatTurn;
import dev.jazzybyte.onseoul.chat.port.out.AiServiceStreamPort;
import dev.jazzybyte.onseoul.chat.port.out.AiStreamEvent;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.instrumentation.annotations.WithSpan;
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
import java.util.concurrent.atomic.AtomicLong;

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
    @WithSpan("ai.chat.stream")
    public Flux<AiStreamEvent> stream(String question, long roomId, long messageId, Double lat, Double lng,
                                      List<ChatTurn> history, Carryover carryover) {
        // 진입 span(@WithSpan으로 생성됨)에 식별 속성 부여. PII 보호: question 평문은 넣지 않는다.
        Span span = Span.current();
        span.setAttribute("chat.room_id", roomId);
        span.setAttribute("chat.message_id", messageId);

        List<AiChatRequest.Turn> turns = (history == null ? List.<ChatTurn>of() : history).stream()
                .map(t -> new AiChatRequest.Turn(t.role(), t.content()))
                .toList();
        Carryover safeCarryover = carryover == null ? Carryover.empty() : carryover;
        // nested 전면 전환: 직전 ASSISTANT의 working_set(opaque JSON)을 prev_working_set 객체로 회신한다.
        // working_set이 null(구 메시지/첫 턴)이면 prev_working_set을 싣지 않는다(AI 현행 동작 폴백 — 하위호환).
        JsonNode prevWorkingSet = parseWorkingSet(safeCarryover.workingSet());
        span.setAttribute("chat.history_size", turns.size());
        AiChatRequest body = new AiChatRequest(roomId, messageId, question, lat, lng, turns, prevWorkingSet);
        // PII 보호: 질문/대화 content 평문은 로깅하지 않고 식별자와 건수만 INFO로 남긴다.
        log.info("[Chat] 스트림 요청 to AI 서비스 - roomId={}, messageId={}, historySize={}, prevWorkingSet={}",
                roomId, messageId, turns.size(), prevWorkingSet != null);
        // [임시 개발 진단] AI 서비스로 보내는 요청 본문 전체(question·history content·prev_working_set 포함)를
        // DEBUG로 기록한다. 사용자 질문 평문이 포함되므로 개발 환경 한정이며, 안정화 후 제거할 것.
        if (log.isDebugEnabled()) {
            try {
                log.debug("[Chat] AI 요청 payload(roomId={}, messageId={}): {}",
                        roomId, messageId, OBJECT_MAPPER.writeValueAsString(body));
            } catch (Exception e) {
                log.debug("[Chat] AI 요청 payload 직렬화 실패: {}", e.getMessage());
            }
        }

        // 스트림 수신 이벤트를 기록할 span 참조를 클로저로 캡처(구독 스레드 전환 시 컨텍스트 유실 방지).
        // 주의: 재시도(retry/repeat) 미도입 전제. 재구독이 생기면 seq가 누적되어 span event가 중복되므로
        // retry 추가 시 seq/streamSpan을 구독별로 재생성(Flux.defer 등)해야 한다.
        final Span streamSpan = span;
        final AtomicLong seq = new AtomicLong(0);

        return webClient.post()
                .uri("/chat/stream")
                .contentType(MediaType.APPLICATION_JSON)
                .accept(MediaType.TEXT_EVENT_STREAM)
                .bodyValue(body)
                .retrieve()
                .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {})
                .timeout(Duration.ofSeconds(properties.streamTimeoutSeconds()))
                .doOnNext(sse -> {
                    long n = seq.incrementAndGet();
                    SseSpanEventRecorder.record(streamSpan, n, sse);
                    streamSpan.setAttribute("sse.event_count", n); // 스트림 활성 중 갱신 — 종료 span 기록 회피
                })
                .mapNotNull(this::toStreamEvent)
                .onErrorMap(TimeoutException.class,
                        e -> new OnSeoulApiException(ErrorCode.AI_SERVICE_TIMEOUT,
                                "AI 서비스 스트림 타임아웃: " + properties.streamTimeoutSeconds() + "초 초과", e))
                .onErrorMap(e -> !(e instanceof OnSeoulApiException),
                        e -> new OnSeoulApiException(ErrorCode.AI_SERVICE_ERROR,
                                "AI 서비스 스트림 오류: " + e.getMessage(), e));
    }

    /**
     * 직전 ASSISTANT의 working_set(opaque JSON 문자열)을 prev_working_set 회신용 JsonNode로 변환한다.
     * null/blank/파싱 실패면 null을 반환해 prev_working_set을 생략한다(@JsonInclude(NON_NULL) — AI 현행 동작 폴백).
     */
    private JsonNode parseWorkingSet(String workingSetJson) {
        if (workingSetJson == null || workingSetJson.isBlank()) {
            return null;
        }
        try {
            return OBJECT_MAPPER.readTree(workingSetJson);
        } catch (Exception e) {
            log.debug("[Chat] working_set carryover 파싱 실패 - prev_working_set 생략으로 폴백");
            return null;
        }
    }

    /**
     * SSE data를 {@link AiStreamEvent}로 변환한다. data가 없는 keep-alive 프레임은 null로 걸러진다.
     *
     * <p>final 식별: data가 JSON 객체이고 {@code answer} 키를 가지며 {@code error} 키가 없을 때.
     * AI 서비스의 {@code workflow_error}/{@code error} 이벤트도 answer를 담을 수 있으나 {@code error}
     * 키가 함께 있으므로 final로 저장하지 않는다(이력에는 정상 답변만 남긴다). final이면 service_cards/intent와
     * 함께 {@code prev_working_set}(opaque 봉투)도 캡처해 다음 턴 carryover로 verbatim 회신한다.
     *
     * <p>decision 식별: data가 JSON 객체이고 {@code "event":"decision"}이며 {@code answer} 키가 없을 때.
     * decision payload(action/routes/user_rationale/sources) 전체를 opaque로 캡처해 final과 함께 저장한다.
     * triage가 LLM 분류한 턴에만 1회 도착할 수 있고(미수신 가능, 하위호환), final보다 먼저 온다.
     *
     * <p>원본 data는 final/decision/progress 어떤 이벤트든 그대로 프론트로 relay된다.
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
                JsonNode cards = node.get("service_cards");
                String serviceCardsJson = (cards == null || cards.isNull())
                        ? null
                        : OBJECT_MAPPER.writeValueAsString(cards);
                JsonNode intentNode = node.get("intent");
                String intent = (intentNode == null || intentNode.isNull()) ? null : intentNode.asText();
                // prev_working_set 봉투를 opaque로 캡처(키 부재/null이면 null). 내부 구조 해석 금지.
                JsonNode workingSetNode = node.get("prev_working_set");
                String workingSetJson = (workingSetNode == null || workingSetNode.isNull())
                        ? null
                        : OBJECT_MAPPER.writeValueAsString(workingSetNode);
                return AiStreamEvent.finalEvent(data, answer.isNull() ? "" : answer.asText(),
                        serviceCardsJson, intent, workingSetJson);
            }
            if (node.isObject() && !node.has("answer") && isDecisionEvent(node)) {
                // decision payload 전체를 opaque로 캡처(action/routes/user_rationale/sources). raw도 그대로 relay.
                return AiStreamEvent.decisionEvent(data, data);
            }
        } catch (Exception e) {
            // JSON이 아니거나 파싱 실패 — relay 전용 이벤트로 취급(프론트 스트림에는 영향 없음).
            log.debug("[Chat] SSE data를 JSON으로 파싱하지 못해 relay 전용으로 처리합니다.");
        }
        return AiStreamEvent.relay(data);
    }

    /** payload의 {@code "event"} 필드가 "decision"이면 decision 이벤트로 식별한다. */
    private static boolean isDecisionEvent(JsonNode node) {
        JsonNode eventNode = node.get("event");
        return eventNode != null && !eventNode.isNull() && "decision".equals(eventNode.asText());
    }
}
