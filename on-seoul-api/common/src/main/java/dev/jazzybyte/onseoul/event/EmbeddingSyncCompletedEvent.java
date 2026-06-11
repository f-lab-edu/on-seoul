package dev.jazzybyte.onseoul.event;

import java.time.Instant;

/**
 * 임베딩 동기화 단계 완료 이벤트.
 *
 * <p>collection 모듈의 임베딩 동기화 워커가 발행하고 notification 모듈과 collection 모듈(AI 캐시 flush)이 구독한다.
 * common 모듈에 위치시켜 두 모듈이 서로 직접 의존하지 않고 이벤트만 공유한다.
 *
 * <p>임베딩 동기화가 성공하든 실패하든(best-effort) 발행된다.
 * AI 서비스 임베딩 동기화 실패가 알림 발송을 막아서는 안 되기 때문이다.
 * 이 이벤트로 처리 순서를 수집 → 임베딩 동기화 → (알림 / AI 캐시 flush) 으로 보장한다.
 *
 * <p>{@code runStartedAt}: 이번 수집 run이 시작된 시각({@link CollectionCompletedEvent#runStartedAt()}을 그대로 전파).
 * AI 캐시 flush 핸들러가 {@code service_change_log.changed_at >= runStartedAt} 조건으로
 * "이번 run에 변경이 있었는지"를 판정해, 변경이 있을 때만 flush한다(변경 0이면 생략 → thundering herd 방지).
 *
 * @param runStartedAt 수집 run 시작 시각
 */
public record EmbeddingSyncCompletedEvent(Instant runStartedAt) {
}
