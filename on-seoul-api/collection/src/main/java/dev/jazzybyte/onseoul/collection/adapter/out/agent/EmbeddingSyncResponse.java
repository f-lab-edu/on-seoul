package dev.jazzybyte.onseoul.collection.adapter.out.agent;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

/**
 * AI 서비스 {@code POST /embeddings/services/sync} 응답 본문 (202).
 * 예: {@code { "accepted": {"upsert": 3, "delete": 1} }}
 */
@JsonIgnoreProperties(ignoreUnknown = true)
record EmbeddingSyncResponse(Accepted accepted) {

    @JsonIgnoreProperties(ignoreUnknown = true)
    record Accepted(int upsert, int delete) {
    }
}
