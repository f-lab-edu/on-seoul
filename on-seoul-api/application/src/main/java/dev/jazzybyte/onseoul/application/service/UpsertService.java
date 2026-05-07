package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.model.ChangeType;
import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;
import dev.jazzybyte.onseoul.domain.model.ServiceChangeLog;
import dev.jazzybyte.onseoul.domain.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.domain.port.out.SavePublicServicePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveServiceChangeLogPort;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;
import java.util.stream.Collectors;

@Slf4j
@Service
@RequiredArgsConstructor
public class UpsertService {

    private final LoadPublicServicePort loadPublicServicePort;
    private final SavePublicServicePort savePublicServicePort;
    private final SaveServiceChangeLogPort saveServiceChangeLogPort;

    @Transactional
    public UpsertResult upsert(List<PublicServiceReservation> incoming, Long collectionId) {
        if (incoming.isEmpty()) {
            return UpsertResult.empty();
        }

        List<String> incomingIds = incoming.stream()
                .map(PublicServiceReservation::getServiceId)
                .toList();

        Map<String, PublicServiceReservation> existingMap = loadPublicServicePort
                .findAllByServiceIdIn(incomingIds)
                .stream()
                .collect(Collectors.toMap(PublicServiceReservation::getServiceId, Function.identity()));

        int newCount = 0, updatedCount = 0, unchangedCount = 0;
        List<ServiceChangeLog> changeLogs = new ArrayList<>();

        for (PublicServiceReservation entity : incoming) {
            PublicServiceReservation existing = existingMap.get(entity.getServiceId());

            if (existing == null) {
                savePublicServicePort.save(entity);
                newCount++;
                log.debug("NEW: serviceId={}", entity.getServiceId());

            } else if (isCoreFieldChanged(existing, entity)) {
                changeLogs.addAll(buildChangeLogs(existing, entity, collectionId));
                existing.update(entity);
                savePublicServicePort.save(existing);
                updatedCount++;
                log.debug("UPDATED: serviceId={}", entity.getServiceId());

            } else {
                unchangedCount++;
            }
        }

        if (!changeLogs.isEmpty()) {
            saveServiceChangeLogPort.saveAll(changeLogs);
        }

        log.info("Upsert 완료 — new={}, updated={}, unchanged={}", newCount, updatedCount, unchangedCount);
        return new UpsertResult(newCount, updatedCount, unchangedCount);
    }

    private boolean isCoreFieldChanged(PublicServiceReservation existing,
                                        PublicServiceReservation incoming) {
        return !Objects.equals(existing.getServiceStatus(), incoming.getServiceStatus())
                || !Objects.equals(existing.getReceiptStartDt(), incoming.getReceiptStartDt())
                || !Objects.equals(existing.getReceiptEndDt(), incoming.getReceiptEndDt());
    }

    private List<ServiceChangeLog> buildChangeLogs(PublicServiceReservation existing,
                                                    PublicServiceReservation incoming,
                                                    Long collectionId) {
        List<ServiceChangeLog> logs = new ArrayList<>();

        if (!Objects.equals(existing.getServiceStatus(), incoming.getServiceStatus())) {
            logs.add(changeLog(existing.getServiceId(), collectionId, "serviceStatus",
                    existing.getServiceStatus(), incoming.getServiceStatus()));
        }
        if (!Objects.equals(existing.getReceiptStartDt(), incoming.getReceiptStartDt())) {
            logs.add(changeLog(existing.getServiceId(), collectionId, "receiptStartDt",
                    String.valueOf(existing.getReceiptStartDt()),
                    String.valueOf(incoming.getReceiptStartDt())));
        }
        if (!Objects.equals(existing.getReceiptEndDt(), incoming.getReceiptEndDt())) {
            logs.add(changeLog(existing.getServiceId(), collectionId, "receiptEndDt",
                    String.valueOf(existing.getReceiptEndDt()),
                    String.valueOf(incoming.getReceiptEndDt())));
        }
        return logs;
    }

    private ServiceChangeLog changeLog(String serviceId, Long collectionId,
                                        String fieldName, String oldValue, String newValue) {
        return ServiceChangeLog.builder()
                .serviceId(serviceId)
                .collectionId(collectionId)
                .changeType(ChangeType.UPDATED)
                .fieldName(fieldName)
                .oldValue(oldValue)
                .newValue(newValue)
                .build();
    }

    public record UpsertResult(int newCount, int updatedCount, int unchangedCount) {
        public static UpsertResult empty() {
            return new UpsertResult(0, 0, 0);
        }
    }
}
