package dev.jazzybyte.onseoul.adapter.out.persistence.reservation;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.LocalTime;

@Entity
@Table(name = "public_service_reservations")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
class PublicServiceReservationJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "service_id", nullable = false, unique = true, length = 30)
    private String serviceId;

    @Column(name = "service_gubun", length = 20)
    private String serviceGubun;

    @Column(name = "max_class_name", length = 50)
    private String maxClassName;

    @Column(name = "min_class_name", length = 50)
    private String minClassName;

    @Column(name = "service_name", nullable = false, length = 200)
    private String serviceName;

    @Column(name = "service_status", nullable = false, length = 20)
    private String serviceStatus;

    @Column(name = "prev_service_status", length = 20)
    private String prevServiceStatus;

    @Column(name = "payment_type", length = 50)
    private String paymentType;

    @Column(name = "target_info", length = 200)
    private String targetInfo;

    @Column(name = "service_url", columnDefinition = "TEXT")
    private String serviceUrl;

    @Column(name = "image_url", columnDefinition = "TEXT")
    private String imageUrl;

    @Column(name = "detail_content", columnDefinition = "TEXT")
    private String detailContent;

    @Column(name = "tel_no", length = 20)
    private String telNo;

    @Column(name = "place_name", length = 200)
    private String placeName;

    @Column(name = "area_name", length = 50)
    private String areaName;

    @Column(name = "coord_x", precision = 20, scale = 15)
    private BigDecimal coordX;

    @Column(name = "coord_y", precision = 20, scale = 15)
    private BigDecimal coordY;

    @Column(name = "service_open_start_dt")
    private LocalDateTime serviceOpenStartDt;

    @Column(name = "service_open_end_dt")
    private LocalDateTime serviceOpenEndDt;

    @Column(name = "receipt_start_dt")
    private LocalDateTime receiptStartDt;

    @Column(name = "receipt_end_dt")
    private LocalDateTime receiptEndDt;

    @Column(name = "use_time_start")
    private LocalTime useTimeStart;

    @Column(name = "use_time_end")
    private LocalTime useTimeEnd;

    @Column(name = "cancel_std_type", length = 30)
    private String cancelStdType;

    @Column(name = "cancel_std_days")
    private Short cancelStdDays;

    @CreationTimestamp
    @Column(name = "first_collected_at", nullable = false, updatable = false)
    private LocalDateTime firstCollectedAt;

    @Column(name = "last_synced_at", nullable = false)
    private LocalDateTime lastSyncedAt;

    @Column(name = "deleted_at")
    private LocalDateTime deletedAt;

    PublicServiceReservationJpaEntity(String serviceId, String serviceGubun, String maxClassName,
                                      String minClassName, String serviceName, String serviceStatus,
                                      String prevServiceStatus, String paymentType, String targetInfo,
                                      String serviceUrl, String imageUrl, String detailContent,
                                      String telNo, String placeName, String areaName,
                                      BigDecimal coordX, BigDecimal coordY,
                                      LocalDateTime serviceOpenStartDt, LocalDateTime serviceOpenEndDt,
                                      LocalDateTime receiptStartDt, LocalDateTime receiptEndDt,
                                      LocalTime useTimeStart, LocalTime useTimeEnd,
                                      String cancelStdType, Short cancelStdDays,
                                      LocalDateTime lastSyncedAt, LocalDateTime deletedAt) {
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
        this.lastSyncedAt = lastSyncedAt != null ? lastSyncedAt : LocalDateTime.now();
        this.deletedAt = deletedAt;
    }

    void update(String serviceStatus, String prevServiceStatus, String serviceName, String placeName,
                LocalDateTime receiptStartDt, LocalDateTime receiptEndDt, String serviceUrl,
                String imageUrl, String detailContent, BigDecimal coordX, BigDecimal coordY) {
        this.prevServiceStatus = prevServiceStatus;
        this.serviceStatus = serviceStatus;
        this.serviceName = serviceName;
        this.placeName = placeName;
        this.receiptStartDt = receiptStartDt;
        this.receiptEndDt = receiptEndDt;
        this.serviceUrl = serviceUrl;
        this.imageUrl = imageUrl;
        this.detailContent = detailContent;
        this.coordX = coordX;
        this.coordY = coordY;
        this.lastSyncedAt = LocalDateTime.now();
    }

    void softDelete() {
        this.deletedAt = LocalDateTime.now();
    }

    void updateCoords(BigDecimal x, BigDecimal y) {
        this.coordX = x;
        this.coordY = y;
        this.lastSyncedAt = LocalDateTime.now();
    }
}
