package dev.jazzybyte.onseoul.chat.adapter.out.agent;

import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.SpanContext;
import org.junit.jupiter.api.Test;
import org.springframework.http.codec.ServerSentEvent;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;

class SseSpanEventRecorderTest {

    @Test
    void answer키가_있으면_final로_분류한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder()
                .data("{\"answer\":\"안녕하세요\"}").build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("final");
    }

    @Test
    void event_decision이면_decision으로_분류한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder()
                .data("{\"event\":\"decision\",\"action\":\"route\"}").build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("decision");
    }

    @Test
    void error키가_있으면_error로_분류한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder()
                .data("{\"answer\":\"\",\"error\":\"boom\"}").build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("error");
    }

    @Test
    void data가_null이면_keepalive로_분류한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder().data(null).build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("keepalive");
    }

    @Test
    void JSON이_아니면_relay로_분류한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder().data("plain text").build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("relay");
    }

    @Test
    void event_progress이면_progress로_분류한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder()
                .data("{\"event\":\"progress\",\"node\":\"router\"}").build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("progress");
    }

    // ── 추가 엣지케이스 (QA 보강) ─────────────────────────────

    @Test
    void 유효한_JSON이지만_객체가_아니면_relay로_분류한다() {
        // readTree는 성공하지만 isObject()=false → catch가 아니라 명시적 relay 분기를 탄다.
        ServerSentEvent<String> array = ServerSentEvent.<String>builder().data("[1,2,3]").build();
        ServerSentEvent<String> number = ServerSentEvent.<String>builder().data("42").build();
        ServerSentEvent<String> str = ServerSentEvent.<String>builder().data("\"just-a-string\"").build();
        assertThat(SseSpanEventRecorder.classify(array)).isEqualTo("relay");
        assertThat(SseSpanEventRecorder.classify(number)).isEqualTo("relay");
        assertThat(SseSpanEventRecorder.classify(str)).isEqualTo("relay");
    }

    @Test
    void event필드가_없는_객체는_기본_progress로_분류한다() {
        // answer/error/event 모두 없는 객체 → default fallthrough(return "progress").
        ServerSentEvent<String> noEvent = ServerSentEvent.<String>builder()
                .data("{\"step\":\"routing\",\"message\":\"분석 중\"}").build();
        assertThat(SseSpanEventRecorder.classify(noEvent)).isEqualTo("progress");
    }

    @Test
    void event필드가_명시적_null이면_기본_progress로_분류한다() {
        ServerSentEvent<String> nullEvent = ServerSentEvent.<String>builder()
                .data("{\"event\":null,\"step\":\"searching\"}").build();
        assertThat(SseSpanEventRecorder.classify(nullEvent)).isEqualTo("progress");
    }

    @Test
    void error키와_answer키가_모두_있으면_error를_우선한다() {
        // 우선순위 고정: error 검사가 answer 검사보다 먼저이므로 error를 반환해야 한다.
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder()
                .data("{\"answer\":\"폴백 답변\",\"error\":\"boom\"}").build();
        assertThat(SseSpanEventRecorder.classify(sse)).isEqualTo("error");
    }

    // ── record() 가드 ──────────────────────────────────────

    @Test
    void record_span이_null이면_NPE없이_무시한다() {
        ServerSentEvent<String> sse = ServerSentEvent.<String>builder().data("{\"answer\":\"a\"}").build();
        assertThatCode(() -> SseSpanEventRecorder.record(null, 1L, sse)).doesNotThrowAnyException();
    }

    @Test
    void record_spanContext가_invalid면_addEvent를_호출하지_않는다() {
        Span span = mock(Span.class);
        SpanContext ctx = mock(SpanContext.class);
        when(span.getSpanContext()).thenReturn(ctx);
        when(ctx.isValid()).thenReturn(false);

        ServerSentEvent<String> sse = ServerSentEvent.<String>builder().data("{\"answer\":\"a\"}").build();
        assertThatCode(() -> SseSpanEventRecorder.record(span, 1L, sse)).doesNotThrowAnyException();
        verify(span, never()).addEvent(eq("sse.received"), any(io.opentelemetry.api.common.Attributes.class));
    }

    @Test
    void record_span이_valid면_sse_received_이벤트를_추가한다() {
        Span span = mock(Span.class);
        SpanContext ctx = mock(SpanContext.class);
        when(span.getSpanContext()).thenReturn(ctx);
        when(ctx.isValid()).thenReturn(true);

        ServerSentEvent<String> sse = ServerSentEvent.<String>builder().data("{\"answer\":\"a\"}").build();
        SseSpanEventRecorder.record(span, 7L, sse);
        verify(span).addEvent(eq("sse.received"), any(io.opentelemetry.api.common.Attributes.class));
    }
}
