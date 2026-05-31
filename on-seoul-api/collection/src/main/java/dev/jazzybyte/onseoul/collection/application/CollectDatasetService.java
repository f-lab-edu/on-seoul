package dev.jazzybyte.onseoul.collection.application;

import dev.jazzybyte.onseoul.collection.application.UpsertService.UpsertResult;
import dev.jazzybyte.onseoul.collection.domain.ApiSourceCatalog;
import dev.jazzybyte.onseoul.collection.domain.ChangeType;
import dev.jazzybyte.onseoul.collection.domain.CollectionHistory;
import dev.jazzybyte.onseoul.collection.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.collection.domain.ServiceChangeLog;
import dev.jazzybyte.onseoul.collection.port.in.CollectDatasetUseCase;
import dev.jazzybyte.onseoul.collection.port.out.LoadApiSourceCatalogPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.collection.port.out.SaveCollectionHistoryPort;
import dev.jazzybyte.onseoul.collection.port.out.SavePublicServicePort;
import dev.jazzybyte.onseoul.collection.port.out.SaveServiceChangeLogPort;
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
    private final SaveServiceChangeLogPort saveServiceChangeLogPort;

    @Override
    public void collectAll() {
        List<ApiSourceCatalog> sources = catalogPort.findAllByActiveTrue();
        if (sources.isEmpty()) {
            log.info("활성 소스 없음 — 수집 스킵");
            return;
        }

        log.info("수집 시작 — 대상 소스 {}개", sources.size());

        Set<String> allSeenServiceIds = new HashSet<>();
        // deletion sweep은 소스 횡단 작업이라 자체 collection_id가 없다.
        // service_change_log.collection_id 가 NOT NULL 이므로 이번 run에서 생성된
        // 첫 번째 collection_id 를 DELETED 행의 참조 collection_id 로 재사용한다.
        Long firstCollectionId = null;
        boolean allSucceeded = true;

        for (ApiSourceCatalog source : sources) {
            Long collectionId = collectOne(source, allSeenServiceIds);
            if (collectionId == null) {
                allSucceeded = false;
            } else if (firstCollectionId == null) {
                firstCollectionId = collectionId;
            }
        }

        if (allSucceeded) {
            performDeletionSweep(allSeenServiceIds, firstCollectionId);
        } else {
            log.warn("일부 소스 수집 실패 — deletion sweep 건너뜀");
        }

        geocodingService.fillMissingCoords();

        log.info("수집 완료");
    }

    /**
     * @return 수집에 성공하면 해당 소스의 collection_id, 실패하면 {@code null}.
     */
    private Long collectOne(ApiSourceCatalog source, Set<String> allSeenServiceIds) {
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
            return history.getId();

        } catch (Exception e) {
            int durationMs = (int) (System.currentTimeMillis() - startMs);
            // 일관성을 위해 반환값을 재할당(기존 행 update). 이후 history는 사용되지 않는다.
            history.fail(e.getMessage(), durationMs);
            history = historyPort.save(history);
            log.error("소스 수집 실패 — datasetId={}, error={}", source.getDatasetId(), e.getMessage(), e);
            return null;
        }
    }

    private void performDeletionSweep(Set<String> seenServiceIds, Long collectionId) {
        List<PublicServiceReservation> toDelete = loadPublicServicePort.findAllByDeletedAtIsNull()
                .stream()
                .filter(r -> !seenServiceIds.contains(r.getServiceId()))
                .toList();

        if (toDelete.isEmpty()) {
            return;
        }

        toDelete.forEach(PublicServiceReservation::softDelete);
        savePublicServicePort.saveAll(toDelete);

        // 삭제된 service_id 1건당 change_type=DELETED 행 1건 — 임베딩 동기화의 delete 대상 식별 소스.
        // collectionId는 이번 run에서 생성된 첫 collection_id(소스 횡단 sweep이라 자체 id가 없음).
        if (collectionId != null) {
            List<ServiceChangeLog> deleteLogs = toDelete.stream()
                    .map(r -> ServiceChangeLog.builder()
                            .serviceId(r.getServiceId())
                            .collectionId(collectionId)
                            .changeType(ChangeType.DELETED)
                            .build())
                    .toList();
            saveServiceChangeLogPort.saveAll(deleteLogs);
        } else {
            log.warn("Deletion sweep — collectionId 없음, DELETED change_log 기록 생략");
        }

        log.info("Deletion sweep — soft-deleted {}건", toDelete.size());
    }
}
