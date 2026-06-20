package dev.jazzybyte.onseoul.collection.adapter.out.agent;

import dev.jazzybyte.onseoul.collection.port.out.EmbeddingSyncPort;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.instrumentation.annotations.WithSpan;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.util.List;

/**
 * AI 서비스 {@code POST /embeddings/services/sync} 호출 어댑터.
 *
 * <p>변경된 service_id를 upsert/delete로 분류해 임베딩 동기화를 요청한다.
 * TemplateAgentClient와 동일한 WebClient/타임아웃 패턴을 따른다.
 *
 * <p>호출 전 가드: upsert·delete가 모두 비면 호출하지 않는다(AI 서비스 422 회피).
 * 청크 분할(합계 ≤ 500)은 호출자(EmbeddingSyncWorker)가 책임진다.
 */
@Slf4j
@Component
class EmbeddingSyncClient implements EmbeddingSyncPort {

    private final WebClient webClient;
    private final EmbeddingSyncProperties properties;

    EmbeddingSyncClient(@Qualifier("embeddingSyncWebClient") WebClient webClient,
                        EmbeddingSyncProperties properties) {
        this.webClient = webClient;
        this.properties = properties;
    }

    @Override
    @WithSpan("ai.embedding.sync")
    public void sync(List<String> upsert, List<String> delete) {
        if (upsert.isEmpty() && delete.isEmpty()) {
            // 빈배열 가드: 둘 다 비면 AI 서비스가 422를 반환하므로 호출하지 않는다.
            log.debug("[EmbeddingSync] upsert/delete 모두 비어 있음 — AI 호출 생략");
            return;
        }
        Span.current().setAttribute("embedding.upsert_count", upsert.size());
        Span.current().setAttribute("embedding.delete_count", delete.size());

        EmbeddingSyncResponse response = webClient.post()
                .uri("/embeddings/services/sync")
                .contentType(MediaType.APPLICATION_JSON)
                .bodyValue(new EmbeddingSyncRequest(upsert, delete))
                .retrieve()
                .bodyToMono(EmbeddingSyncResponse.class)
                .timeout(Duration.ofSeconds(properties.embeddingSyncTimeoutSeconds()))
                .block();

        if (response != null && response.accepted() != null) {
            log.info("[EmbeddingSync] AI 동기화 수락: upsert={}, delete={}",
                    response.accepted().upsert(), response.accepted().delete());
        } else {
            log.info("[EmbeddingSync] AI 동기화 요청 완료(응답 본문 없음): upsert={}, delete={}",
                    upsert.size(), delete.size());
        }
    }
}
