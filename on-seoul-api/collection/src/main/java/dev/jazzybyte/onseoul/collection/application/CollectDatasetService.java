package dev.jazzybyte.onseoul.collection.application;

import dev.jazzybyte.onseoul.collection.application.UpsertService.UpsertResult;
import dev.jazzybyte.onseoul.collection.domain.ApiSourceCatalog;
import dev.jazzybyte.onseoul.collection.domain.CollectionHistory;
import dev.jazzybyte.onseoul.collection.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.collection.port.in.CollectDatasetUseCase;
import dev.jazzybyte.onseoul.collection.port.out.LoadApiSourceCatalogPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.collection.port.out.SaveCollectionHistoryPort;
import dev.jazzybyte.onseoul.collection.port.out.SavePublicServicePort;
import dev.jazzybyte.onseoul.collection.port.out.SeoulDatasetFetchPort;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

@Slf4j
@Service
@RequiredArgsConstructor
public class CollectDatasetService implements CollectDatasetUseCase {

    private final LoadApiSourceCatalogPort catalogPort;
    private final SaveCollectionHistoryPort historyPort;
    private final LoadPublicServicePort loadPublicServicePort;
    private final SavePublicServicePort savePublicServicePort;
    private final SeoulDatasetFetchPort fetchPort;
    private final UpsertService upsertService;
    private final GeocodingService geocodingService;

    @Override
    public void collectAll() {
        List<ApiSourceCatalog> sources = catalogPort.findAllByActiveTrue();
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
        // save()는 생성된 PK가 담긴 새 도메인 객체를 반환하므로 반환값을 재할당해 id를 캡처한다.
        CollectionHistory history = historyPort.save(CollectionHistory.create(source.getId()));

        long startMs = System.currentTimeMillis();
        try {
            List<PublicServiceReservation> entities = fetchPort.fetchAll(source.getApiServicePath());

            entities.stream()
                    .map(PublicServiceReservation::getServiceId)
                    .forEach(allSeenServiceIds::add);

            UpsertResult result = upsertService.upsert(entities, history.getId());
            int durationMs = (int) (System.currentTimeMillis() - startMs);

            // complete()는 in-place 변경 → 같은 id를 가진 history를 다시 저장해 기존 행을 update.
            history.complete(entities.size(), result.newCount(), result.updatedCount(), 0, durationMs);
            history = historyPort.save(history);

            log.info("소스 수집 완료 — datasetId={}, total={}, new={}, updated={}, unchanged={}",
                    source.getDatasetId(), entities.size(),
                    result.newCount(), result.updatedCount(), result.unchangedCount());
            return true;

        } catch (Exception e) {
            int durationMs = (int) (System.currentTimeMillis() - startMs);
            // 일관성을 위해 반환값을 재할당(기존 행 update). 이후 history는 사용되지 않는다.
            history.fail(e.getMessage(), durationMs);
            history = historyPort.save(history);
            log.error("소스 수집 실패 — datasetId={}, error={}", source.getDatasetId(), e.getMessage(), e);
            return false;
        }
    }

    private void performDeletionSweep(Set<String> seenServiceIds) {
        List<PublicServiceReservation> toDelete = loadPublicServicePort.findAllByDeletedAtIsNull()
                .stream()
                .filter(r -> !seenServiceIds.contains(r.getServiceId()))
                .toList();

        if (toDelete.isEmpty()) {
            return;
        }

        toDelete.forEach(PublicServiceReservation::softDelete);
        savePublicServicePort.saveAll(toDelete);
        log.info("Deletion sweep — soft-deleted {}건", toDelete.size());
    }
}
