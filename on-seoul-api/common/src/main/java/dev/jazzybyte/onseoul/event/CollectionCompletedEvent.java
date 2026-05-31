package dev.jazzybyte.onseoul.event;

import java.time.Instant;

/**
 * 공공서비스 예약 데이터 수집 완료 이벤트.
 *
 * <p>collection 모듈이 발행하고 collection 모듈(임베딩 동기화 워커)이 구독한다.
 * common 모듈에 위치시켜 모듈 간 직접 의존 없이 이벤트만 공유한다.
 *
 * <p>수집 도중 일부 소스가 실패하더라도 이벤트는 발행된다.
 * 성공한 소스에서 변경된 데이터에 대한 후속 처리(임베딩 동기화 → 알림)는 진행해야 하기 때문이다.
 *
 * <p>{@code runStartedAt}: 이번 수집 run이 시작된 시각. 임베딩 동기화 워커가
 * {@code service_change_log.changed_at >= runStartedAt} 조건으로 "이번 run의 변경분"을
 * 식별하는 데 사용한다. collectAll()은 소스별로 별도 collection_id를 만들고
 * deletion sweep은 소스 횡단이라 단일 collection_id가 없으므로, timestamp 기반이
 * collection_id 기반보다 견고하다.
 *
 * @param runStartedAt 수집 run 시작 시각(스케줄러가 collectAll() 호출 직전 캡처)
 */
public record CollectionCompletedEvent(Instant runStartedAt) {
}
