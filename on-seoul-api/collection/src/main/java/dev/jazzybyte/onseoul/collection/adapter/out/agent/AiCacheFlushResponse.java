package dev.jazzybyte.onseoul.collection.adapter.out.agent;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;

/**
 * AI 서비스 {@code POST /admin/cache/flush} 응답 본문.
 * 예: {@code { "deleted": 12 }}
 *
 * <p>로깅 시에는 삭제 건수({@code deleted})만 남기고 캐시 키는 노출하지 않는다.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
record AiCacheFlushResponse(Integer deleted) {
}
