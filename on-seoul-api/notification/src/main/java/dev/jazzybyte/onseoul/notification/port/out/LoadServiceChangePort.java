package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.ServiceChange;

import java.time.Instant;
import java.util.List;

public interface LoadServiceChangePort {

    /**
     * serviceId에 대해 since 이후(exclusive)에 기록된 변경 이력을 조회한다.
     * since가 null이면 전체 이력을 반환한다.
     */
    List<ServiceChange> loadSince(String serviceId, Instant since);
}
