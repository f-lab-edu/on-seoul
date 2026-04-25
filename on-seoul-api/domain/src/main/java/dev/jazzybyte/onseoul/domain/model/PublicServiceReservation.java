package dev.jazzybyte.onseoul.domain.model;

import lombok.Builder;
import lombok.Getter;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.LocalTime;

@Getter
public class PublicServiceReservation {

    private Long id;
    private String serviceId;
    private String serviceGubun;
    private String maxClassName;
    private String minClassName;
    private String serviceName;
    private String serviceStatus;
    private String prevServiceStatus;
    private String paymentType;
    private String targetInfo;
    private String serviceUrl;
    private String imageUrl;
    private String detailContent;
    private String telNo;
    private String placeName;
    private String areaName;
    private BigDecimal coordX;
    private BigDecimal coordY;
    private LocalDateTime serviceOpenStartDt;
    private LocalDateTime serviceOpenEndDt;
    private LocalDateTime receiptStartDt;
    private LocalDateTime receiptEndDt;
    private LocalTime useTimeStart;
    private LocalTime useTimeEnd;
    private String cancelStdType;
    private Short cancelStdDays;
    private LocalDateTime firstCollectedAt;
    private LocalDateTime lastSyncedAt;
    private LocalDateTime deletedAt;

    @Builder
    public PublicServiceReservation(Long id, String serviceId, String serviceGubun,
                                    String maxClassName, String minClassName,
                                    String serviceName, String serviceStatus,
                                    String prevServiceStatus, String paymentType,
                                    String targetInfo, String serviceUrl, String imageUrl,
                                    String detailContent, String telNo, String placeName,
                                    String areaName, BigDecimal coordX, BigDecimal coordY,
                                    LocalDateTime serviceOpenStartDt, LocalDateTime serviceOpenEndDt,
                                    LocalDateTime receiptStartDt, LocalDateTime receiptEndDt,
                                    LocalTime useTimeStart, LocalTime useTimeEnd,
                                    String cancelStdType, Short cancelStdDays,
                                    LocalDateTime firstCollectedAt, LocalDateTime lastSyncedAt,
                                    LocalDateTime deletedAt) {
        this.id = id;
        this.serviceId = serviceId;
        this.serviceGubun = serviceGubun;
        this.maxClassName = maxClassName;
        this.minClassName = minClassName;
        this.serviceName = serviceName;
        this.serviceStatus = serviceStatus;
        this.prevServiceStatus = prevServiceStatus;
        this.paymentType = paymentType;
        this.targetInfo = targetInfo;
        this.serviceUrl = serviceUrl;
        this.imageUrl = imageUrl;
        this.detailContent = detailContent;
        this.telNo = telNo;
        this.placeName = placeName;
        this.areaName = areaName;
        this.coordX = coordX;
        this.coordY = coordY;
        this.serviceOpenStartDt = serviceOpenStartDt;
        this.serviceOpenEndDt = serviceOpenEndDt;
        this.receiptStartDt = receiptStartDt;
        this.receiptEndDt = receiptEndDt;
        this.useTimeStart = useTimeStart;
        this.useTimeEnd = useTimeEnd;
        this.cancelStdType = cancelStdType;
        this.cancelStdDays = cancelStdDays;
        this.firstCollectedAt = firstCollectedAt;
        this.lastSyncedAt = lastSyncedAt != null ? lastSyncedAt : LocalDateTime.now();
        this.deletedAt = deletedAt;
    }

    public void update(PublicServiceReservation updated) {
        this.prevServiceStatus = this.serviceStatus;
        this.serviceStatus = updated.serviceStatus;
        this.serviceName = updated.serviceName;
        this.placeName = updated.placeName;
        this.receiptStartDt = updated.receiptStartDt;
        this.receiptEndDt = updated.receiptEndDt;
        this.serviceUrl = updated.serviceUrl;
        this.imageUrl = updated.imageUrl;
        this.detailContent = updated.detailContent;
        this.coordX = updated.coordX;
        this.coordY = updated.coordY;
        this.lastSyncedAt = LocalDateTime.now();
    }

    public void softDelete() {
        this.deletedAt = LocalDateTime.now();
    }

    public void updateCoords(BigDecimal x, BigDecimal y) {
        this.coordX = x;
        this.coordY = y;
        this.lastSyncedAt = LocalDateTime.now();
    }
}
