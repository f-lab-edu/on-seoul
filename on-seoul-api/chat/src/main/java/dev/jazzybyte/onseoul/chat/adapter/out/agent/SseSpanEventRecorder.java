package dev.jazzybyte.onseoul.chat.adapter.out.agent;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.opentelemetry.api.common.Attributes;
import io.opentelemetry.api.trace.Span;
import org.springframework.http.codec.ServerSentEvent;

/**
 * AI 서비스로부터 수신한 SSE 이벤트를 현재 span의 span event로 기록한다.
 *
 * <p>이벤트별 child span 대신 span event를 쓰는 이유: 토큰성 progress 이벤트가 많아
 * span 폭증(카디널리티)을 피하고, 하나의 스트림 span 타임라인에 수신 시점을 점으로 남긴다.
 */
final class SseSpanEventRecorder {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private SseSpanEventRecorder() {
    }

    /** SSE data를 이벤트 타입 문자열로 분류한다(순수 함수, 테스트 대상). */
    static String classify(ServerSentEvent<String> sse) {
        String data = sse.data();
        if (data == null) {
            return "keepalive";
        }
        try {
            JsonNode node = OBJECT_MAPPER.readTree(data);
            if (!node.isObject()) {
                return "relay";
            }
            if (node.has("error")) {
                return "error";
            }
            if (node.has("answer")) {
                return "final";
            }
            JsonNode event = node.get("event");
            if (event != null && !event.isNull()) {
                return event.asText();   // "decision" / "progress" 등 AI가 명시한 타입
            }
            return "progress";
        } catch (Exception e) {
            return "relay";
        }
    }

    /**
     * 현재 활성 span에 수신 이벤트를 span event로 추가한다.
     * span 컨텍스트는 호출 측(doOnNext 클로저)에서 makeCurrent로 활성화되어 있어야 한다.
     * PII 보호: data 본문은 기록하지 않고 타입과 순번만 남긴다.
     */
    static void record(Span span, long seq, ServerSentEvent<String> sse) {
        if (span == null || !span.getSpanContext().isValid()) {
            return;
        }
        span.addEvent("sse.received", Attributes.builder()
                .put("sse.seq", seq)
                .put("sse.event_type", classify(sse))
                .build());
    }
}
