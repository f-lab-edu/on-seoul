package dev.jazzybyte.onseoul.collection.port.out;

import java.time.Instant;
import java.util.List;

/**
 * 이번 수집 run에서 변경된 service_id 목록을 조회한다.
 *
 * <p>임베딩 동기화 워커가 사용한다. "이번 run"은 {@code changed_at >= since} 로 식별한다
 * (run 시작 시각 이후 기록된 모든 NEW/UPDATED/DELETED change_log).
 */
public interface LoadChangedServiceIdsPort {

    /**
     * @param since 이 시각(inclusive) 이후 변경된 행만 대상
     * @return upsert 대상(NEW ∪ UPDATED)과 delete 대상(DELETED)을 분류한 distinct service_id
     */
    ChangedServiceIds loadSince(Instant since);

    record ChangedServiceIds(List<String> upsert, List<String> delete) {
        public boolean isEmpty() {
            return upsert.isEmpty() && delete.isEmpty();
        }
    }
}
