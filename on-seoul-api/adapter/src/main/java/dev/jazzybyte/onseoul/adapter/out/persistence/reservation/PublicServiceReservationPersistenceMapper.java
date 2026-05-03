package dev.jazzybyte.onseoul.adapter.out.persistence.reservation;

import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;
import org.springframework.stereotype.Component;

@Component
class PublicServiceReservationPersistenceMapper {

    PublicServiceReservation toDomain(PublicServiceReservationJpaEntity e) {
        return PublicServiceReservation.builder()
                .id(e.getId())
                .serviceId(e.getServiceId())
                .serviceGubun(e.getServiceGubun())
                .maxClassName(e.getMaxClassName())
                .minClassName(e.getMinClassName())
                .serviceName(e.getServiceName())
                .serviceStatus(e.getServiceStatus())
                .prevServiceStatus(e.getPrevServiceStatus())
                .paymentType(e.getPaymentType())
                .targetInfo(e.getTargetInfo())
                .serviceUrl(e.getServiceUrl())
                .imageUrl(e.getImageUrl())
                .detailContent(e.getDetailContent())
                .telNo(e.getTelNo())
                .placeName(e.getPlaceName())
                .areaName(e.getAreaName())
                .coordX(e.getCoordX())
                .coordY(e.getCoordY())
                .serviceOpenStartDt(e.getServiceOpenStartDt())
                .serviceOpenEndDt(e.getServiceOpenEndDt())
                .receiptStartDt(e.getReceiptStartDt())
                .receiptEndDt(e.getReceiptEndDt())
                .useTimeStart(e.getUseTimeStart())
                .useTimeEnd(e.getUseTimeEnd())
                .cancelStdType(e.getCancelStdType())
                .cancelStdDays(e.getCancelStdDays())
                .firstCollectedAt(e.getFirstCollectedAt())
                .lastSyncedAt(e.getLastSyncedAt())
                .deletedAt(e.getDeletedAt())
                .build();
    }

    PublicServiceReservationJpaEntity toEntity(PublicServiceReservation d) {
        return new PublicServiceReservationJpaEntity(
                d.getServiceId(), d.getServiceGubun(), d.getMaxClassName(),
                d.getMinClassName(), d.getServiceName(), d.getServiceStatus(),
                d.getPrevServiceStatus(), d.getPaymentType(), d.getTargetInfo(),
                d.getServiceUrl(), d.getImageUrl(), d.getDetailContent(),
                d.getTelNo(), d.getPlaceName(), d.getAreaName(),
                d.getCoordX(), d.getCoordY(),
                d.getServiceOpenStartDt(), d.getServiceOpenEndDt(),
                d.getReceiptStartDt(), d.getReceiptEndDt(),
                d.getUseTimeStart(), d.getUseTimeEnd(),
                d.getCancelStdType(), d.getCancelStdDays(),
                d.getLastSyncedAt(), d.getDeletedAt()
        );
    }

    PublicServiceReservationJpaEntity updateEntity(PublicServiceReservationJpaEntity entity,
                                                    PublicServiceReservation domain) {
        entity.update(
                domain.getServiceStatus(),
                domain.getPrevServiceStatus(),
                domain.getServiceName(),
                domain.getPlaceName(),
                domain.getReceiptStartDt(),
                domain.getReceiptEndDt(),
                domain.getServiceUrl(),
                domain.getImageUrl(),
                domain.getDetailContent(),
                domain.getCoordX(),
                domain.getCoordY()
        );
        if (domain.getDeletedAt() != null) {
            entity.softDelete();
        }
        if (domain.getCoordX() != null && domain.getCoordY() != null
                && entity.getCoordX() == null && entity.getCoordY() == null) {
            entity.updateCoords(domain.getCoordX(), domain.getCoordY());
        }
        return entity;
    }
}
