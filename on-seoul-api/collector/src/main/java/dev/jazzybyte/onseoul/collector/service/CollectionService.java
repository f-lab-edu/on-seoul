package dev.jazzybyte.onseoul.collector.service;

import dev.jazzybyte.onseoul.collector.PublicServiceRowMapper;
import dev.jazzybyte.onseoul.collector.SeoulOpenApiClient;
import dev.jazzybyte.onseoul.collector.domain.ApiSourceCatalog;
import dev.jazzybyte.onseoul.collector.domain.CollectionHistory;
import dev.jazzybyte.onseoul.collector.dto.UpsertResult;
import dev.jazzybyte.onseoul.collector.repository.ApiSourceCatalogRepository;
import dev.jazzybyte.onseoul.collector.repository.CollectionHistoryRepository;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.repository.PublicServiceReservationRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

@Slf4j
@Service
@RequiredArgsConstructor
public class CollectionService {

    private final ApiSourceCatalogRepository catalogRepository;
    private final CollectionHistoryRepository historyRepository;
    private final PublicServiceReservationRepository reservationRepository;
    private final SeoulOpenApiClient apiClient;
    private final PublicServiceRowMapper rowMapper;
    private final UpsertService upsertService;
    private final GeocodingService geocodingService;

    /**
     * 모든 활성 소스를 순차 수집한다.
     *
     * <p>개별 소스 실패 시 이력에 기록 후 다음 소스로 계속 진행한다.
     * 전체 성공 시에만 DB 잔존 레코드 soft-delete를 수행한다.</p>
     */
    public void collectAll() {
        List<ApiSourceCatalog> sources = catalogRepository.findAllByActiveTrue();
        if (sources.isEmpty()) {
            log.info("활성 소스 없음 — 수집 스킵");
            return;
        }

        log.info("수집 시작 — 대상 소스 {}개", sources.size());

        Set<String> allSeenServiceIds = new HashSet<>();
        boolean allSucceeded = true;

        for (ApiSourceCatalog source : sources) {
            boolean succeeded = collectOne(source, allSeenServiceIds);
            if (!succeeded) {
                allSucceeded = false;
            }
        }

        if (allSucceeded) {
            performDeletionSweep(allSeenServiceIds);
        } else {
            log.warn("일부 소스 수집 실패 — deletion sweep 건너뜀");
        }

        geocodingService.fillMissingCoords();

        log.info("수집 완료");
    }

    private boolean collectOne(ApiSourceCatalog source, Set<String> allSeenServiceIds) {
        CollectionHistory history = CollectionHistory.builder()
                .sourceId(source.getId())
                .build();
        historyRepository.save(history);

        long startMs = System.currentTimeMillis();
        try {
            List<PublicServiceReservation> entities = apiClient.fetchAll(source.getApiServicePath())
                    .stream()
                    .map(rowMapper::toEntity)
                    .filter(java.util.Optional::isPresent)
                    .map(java.util.Optional::get)
                    .toList();

            entities.stream()
                    .map(PublicServiceReservation::getServiceId)
                    .forEach(allSeenServiceIds::add);

            UpsertResult result = upsertService.upsert(entities, history.getId());
            int durationMs = (int) (System.currentTimeMillis() - startMs);

            history.complete(entities.size(), result.newCount(), result.updatedCount(), 0, durationMs);
            historyRepository.save(history);

            log.info("소스 수집 완료 — datasetId={}, total={}, new={}, updated={}, unchanged={}",
                    source.getDatasetId(), entities.size(),
                    result.newCount(), result.updatedCount(), result.unchangedCount());
            return true;

        } catch (Exception e) {
            int durationMs = (int) (System.currentTimeMillis() - startMs);
            history.fail(e.getMessage(), durationMs);
            historyRepository.save(history);
            log.error("소스 수집 실패 — datasetId={}, error={}", source.getDatasetId(), e.getMessage(), e);
            return false;
        }
    }

    /**
     * DB에 남아있지만 이번 수집에서 보이지 않은 레코드를 soft-delete 처리한다.
     * 전체 소스 수집 성공 시에만 호출된다.
     */
    private void performDeletionSweep(Set<String> seenServiceIds) {
        List<PublicServiceReservation> toDelete = reservationRepository.findAllByDeletedAtIsNull()
                .stream()
                .filter(r -> !seenServiceIds.contains(r.getServiceId()))
                .toList();

        if (toDelete.isEmpty()) {
            return;
        }

        toDelete.forEach(PublicServiceReservation::softDelete);
        reservationRepository.saveAll(toDelete);
        log.info("Deletion sweep — soft-deleted {}건", toDelete.size());
    }
}
