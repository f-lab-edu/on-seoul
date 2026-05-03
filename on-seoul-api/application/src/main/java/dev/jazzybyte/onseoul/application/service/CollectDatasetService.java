package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.application.service.UpsertService.UpsertResult;
import dev.jazzybyte.onseoul.domain.model.ApiSourceCatalog;
import dev.jazzybyte.onseoul.domain.model.CollectionHistory;
import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;
import dev.jazzybyte.onseoul.domain.port.in.CollectDatasetUseCase;
import dev.jazzybyte.onseoul.domain.port.out.LoadApiSourceCatalogPort;
import dev.jazzybyte.onseoul.domain.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveCollectionHistoryPort;
import dev.jazzybyte.onseoul.domain.port.out.SavePublicServicePort;
import dev.jazzybyte.onseoul.domain.port.out.SeoulDatasetFetchPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

@Slf4j
@Service
public class CollectDatasetService implements CollectDatasetUseCase {

    private final LoadApiSourceCatalogPort catalogPort;
    private final SaveCollectionHistoryPort historyPort;
    private final LoadPublicServicePort loadPublicServicePort;
    private final SavePublicServicePort savePublicServicePort;
    private final SeoulDatasetFetchPort fetchPort;
    private final UpsertService upsertService;
    private final GeocodingService geocodingService;

    public CollectDatasetService(final LoadApiSourceCatalogPort catalogPort,
                                 final SaveCollectionHistoryPort historyPort,
                                 final LoadPublicServicePort loadPublicServicePort,
                                 final SavePublicServicePort savePublicServicePort,
                                 final SeoulDatasetFetchPort fetchPort,
                                 final UpsertService upsertService,
                                 final GeocodingService geocodingService) {
        this.catalogPort = catalogPort;
        this.historyPort = historyPort;
        this.loadPublicServicePort = loadPublicServicePort;
        this.savePublicServicePort = savePublicServicePort;
        this.fetchPort = fetchPort;
        this.upsertService = upsertService;
        this.geocodingService = geocodingService;
    }

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
        CollectionHistory history = CollectionHistory.create(source.getId());
        historyPort.save(history);

        long startMs = System.currentTimeMillis();
        try {
            List<PublicServiceReservation> entities = fetchPort.fetchAll(source.getApiServicePath());

            entities.stream()
                    .map(PublicServiceReservation::getServiceId)
                    .forEach(allSeenServiceIds::add);

            UpsertResult result = upsertService.upsert(entities, history.getId());
            int durationMs = (int) (System.currentTimeMillis() - startMs);

            history.complete(entities.size(), result.newCount(), result.updatedCount(), 0, durationMs);
            historyPort.save(history);

            log.info("소스 수집 완료 — datasetId={}, total={}, new={}, updated={}, unchanged={}",
                    source.getDatasetId(), entities.size(),
                    result.newCount(), result.updatedCount(), result.unchangedCount());
            return true;

        } catch (Exception e) {
            int durationMs = (int) (System.currentTimeMillis() - startMs);
            history.fail(e.getMessage(), durationMs);
            historyPort.save(history);
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
