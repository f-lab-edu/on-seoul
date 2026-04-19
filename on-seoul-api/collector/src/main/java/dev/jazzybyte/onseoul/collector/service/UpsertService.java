package dev.jazzybyte.onseoul.collector.service;

import dev.jazzybyte.onseoul.collector.domain.ServiceChangeLog;
import dev.jazzybyte.onseoul.collector.dto.UpsertResult;
import dev.jazzybyte.onseoul.collector.enums.ChangeType;
import dev.jazzybyte.onseoul.collector.repository.ServiceChangeLogRepository;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.repository.PublicServiceReservationRepository;
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

    private final PublicServiceReservationRepository reservationRepository;
    private final ServiceChangeLogRepository changeLogRepository;

    /**
     * incoming 엔티티 배치를 NEW / UPDATED / UNCHANGED로 분류하여 DB에 반영한다.
     *
     * @param incoming     API에서 수신·변환된 엔티티 목록
     * @param collectionId 이번 수집 이력 ID (ServiceChangeLog FK)
     * @return 분류별 카운트
     */
    @Transactional
    public UpsertResult upsert(List<PublicServiceReservation> incoming, Long collectionId) {
        if (incoming.isEmpty()) {
            return UpsertResult.empty();
        }

        List<String> incomingIds = incoming.stream()
                .map(PublicServiceReservation::getServiceId)
                .toList();

        Map<String, PublicServiceReservation> existingMap = reservationRepository
                .findAllByServiceIdIn(incomingIds)
                .stream()
                .collect(Collectors.toMap(PublicServiceReservation::getServiceId, Function.identity()));

        int newCount = 0, updatedCount = 0, unchangedCount = 0;
        List<ServiceChangeLog> changeLogs = new ArrayList<>();

        for (PublicServiceReservation entity : incoming) {
            PublicServiceReservation existing = existingMap.get(entity.getServiceId());

            if (existing == null) {
                reservationRepository.save(entity);
                newCount++;
                log.debug("NEW: serviceId={}", entity.getServiceId());

            } else if (isCoreFieldChanged(existing, entity)) {
                changeLogs.addAll(buildChangeLogs(existing, entity, collectionId));
                existing.update(entity);
                reservationRepository.save(existing);
                updatedCount++;
                log.debug("UPDATED: serviceId={}", entity.getServiceId());

            } else {
                unchangedCount++;
            }
        }

        if (!changeLogs.isEmpty()) {
            changeLogRepository.saveAll(changeLogs);
        }

        log.info("Upsert 완료 — new={}, updated={}, unchanged={}", newCount, updatedCount, unchangedCount);
        return new UpsertResult(newCount, updatedCount, unchangedCount);
    }

    /**
     * 변경 판정 기준: serviceStatus, receiptStartDt, receiptEndDt.
     * DTLCONT 등 대형 텍스트는 비교 비용이 커서 제외한다.
     */
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
}
