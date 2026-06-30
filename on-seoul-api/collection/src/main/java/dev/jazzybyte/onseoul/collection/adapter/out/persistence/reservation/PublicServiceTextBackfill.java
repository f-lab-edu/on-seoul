package dev.jazzybyte.onseoul.collection.adapter.out.persistence.reservation;

import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * 기존 public_service_reservations 행의 HTML-escape된 표시 텍스트를 앱 코드로 디코딩하는 일회성 백필.
 *
 * <p>UpsertService를 우회하므로 change_log/알림에 영향 없음. 디코딩은 멱등이라 재실행 안전(변경된 행만 save).
 * 데이터가 소규모(&lt;1000건)라 단일 트랜잭션 1패스로 충분하다.
 */
@Slf4j
@Component
public class PublicServiceTextBackfill {

    private final PublicServiceReservationJpaRepository repository;

    PublicServiceTextBackfill(final PublicServiceReservationJpaRepository repository) {
        this.repository = repository;
    }

    /** @return [처리 건수, 변경 건수] */
    @Transactional
    public BackfillResult run() {
        var all = repository.findAll();
        int processed = 0;
        int changed = 0;
        for (PublicServiceReservationJpaEntity entity : all) {
            processed++;
            if (entity.decodeHtmlEntities()) {
                repository.save(entity);
                changed++;
            }
        }
        log.warn("[TextBackfill] public_service_reservations 디코딩 완료 — 처리={}, 변경={}", processed, changed);
        return new BackfillResult(processed, changed);
    }

    public record BackfillResult(int processed, int changed) {}
}
