package dev.jazzybyte.onseoul.collection.adapter.out.agent;

import java.util.List;

/**
 * AI 서비스 {@code POST /embeddings/services/sync} 요청 본문.
 * 필드명이 그대로 JSON 키({@code upsert}, {@code delete})로 직렬화된다.
 */
record EmbeddingSyncRequest(List<String> upsert, List<String> delete) {
}
