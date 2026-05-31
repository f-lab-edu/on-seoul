package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationSubscription;

import java.util.List;
import java.util.Optional;

public interface LoadSubscriptionPort {

    /**
     * 모든 구독을 한 번에 로드한다.
     *
     * @deprecated 스케줄러는 메모리 효율을 위해 {@link #loadChunk(Long, int)}를 사용한다.
     *             관리/테스트 목적 외의 신규 호출을 지양한다.
     */
    @Deprecated
    List<NotificationSubscription> loadAll();

    /**
     * keyset 기반 청크 조회. {@code id > afterId} 조건으로 {@code limit}건을 {@code id ASC} 순서로 반환한다.
     *
     * <p>반환 크기가 {@code limit}보다 작으면 마지막 페이지다.
     * 반환 크기가 {@code limit}과 같으면 다음 청크가 존재할 수 있으므로
     * 마지막 요소의 id를 다음 {@code afterId}로 전달한다.
     *
     * @param afterId 이 id 이후(exclusive)부터 조회. 0이면 처음부터
     * @param limit   최대 반환 건수
     */
    List<NotificationSubscription> loadChunk(Long afterId, int limit);

    List<NotificationSubscription> loadByUserId(Long userId);

    Optional<NotificationSubscription> loadById(Long id);
}
