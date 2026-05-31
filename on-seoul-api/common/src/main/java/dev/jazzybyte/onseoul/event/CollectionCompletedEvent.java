package dev.jazzybyte.onseoul.event;

/**
 * 공공서비스 예약 데이터 수집 완료 이벤트.
 *
 * <p>collection 모듈이 발행하고 notification 모듈이 구독한다.
 * common 모듈에 위치시켜 두 모듈이 서로 직접 의존하지 않고 이벤트만 공유한다.
 *
 * <p>수집 도중 일부 소스가 실패하더라도 이벤트는 발행된다.
 * 성공한 소스에서 변경된 데이터에 대한 알림은 보내야 하기 때문이다.
 */
public record CollectionCompletedEvent() {
}
