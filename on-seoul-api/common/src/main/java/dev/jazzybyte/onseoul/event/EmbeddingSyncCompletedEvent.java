package dev.jazzybyte.onseoul.event;

/**
 * 임베딩 동기화 단계 완료 이벤트.
 *
 * <p>collection 모듈의 임베딩 동기화 워커가 발행하고 notification 모듈이 구독한다.
 * common 모듈에 위치시켜 두 모듈이 서로 직접 의존하지 않고 이벤트만 공유한다.
 *
 * <p>임베딩 동기화가 성공하든 실패하든(best-effort) 발행된다.
 * AI 서비스 임베딩 동기화 실패가 알림 발송을 막아서는 안 되기 때문이다.
 * 이 이벤트로 처리 순서를 수집 → 임베딩 동기화 → 알림 으로 보장한다.
 */
public record EmbeddingSyncCompletedEvent() {
}
