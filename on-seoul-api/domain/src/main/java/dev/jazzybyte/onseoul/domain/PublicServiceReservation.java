package dev.jazzybyte.onseoul.domain;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
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
public class PublicServiceReservation {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "service_id", nullable = false, unique = true, length = 30)
    private String serviceId;

    // 분류
    @Column(name = "service_gubun", length = 20)
    private String serviceGubun;

    @Column(name = "max_class_name", length = 50)
    private String maxClassName;

    @Column(name = "min_class_name", length = 50)
    private String minClassName;

    // 서비스 기본 정보
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

    // 장소
    @Column(name = "place_name", length = 200)
    private String placeName;

    @Column(name = "area_name", length = 50)
    private String areaName;

    @Column(name = "coord_x", precision = 20, scale = 15)
    private BigDecimal coordX;

    @Column(name = "coord_y", precision = 20, scale = 15)
    private BigDecimal coordY;

    // 서비스 개시 기간
    @Column(name = "service_open_start_dt")
    private LocalDateTime serviceOpenStartDt;

    @Column(name = "service_open_end_dt")
    private LocalDateTime serviceOpenEndDt;

    // 접수 기간
    @Column(name = "receipt_start_dt")
    private LocalDateTime receiptStartDt;

    @Column(name = "receipt_end_dt")
    private LocalDateTime receiptEndDt;

    // 이용 시간
    @Column(name = "use_time_start")
    private LocalTime useTimeStart;

    @Column(name = "use_time_end")
    private LocalTime useTimeEnd;

    // 취소 기준
    @Column(name = "cancel_std_type", length = 30)
    private String cancelStdType;

    @Column(name = "cancel_std_days")
    private Short cancelStdDays;

    // 수집 메타
    @CreationTimestamp
    @Column(name = "first_collected_at", nullable = false, updatable = false)
    private LocalDateTime firstCollectedAt;

    @Column(name = "last_synced_at", nullable = false)
    private LocalDateTime lastSyncedAt;

    @Column(name = "deleted_at")
    private LocalDateTime deletedAt;

    @Builder
    public PublicServiceReservation(String serviceId, String serviceGubun, String maxClassName,
                                    String minClassName, String serviceName, String serviceStatus,
                                    String prevServiceStatus, String paymentType, String targetInfo,
                                    String serviceUrl, String imageUrl, String detailContent,
                                    String telNo, String placeName, String areaName,
                                    BigDecimal coordX, BigDecimal coordY,
                                    LocalDateTime serviceOpenStartDt, LocalDateTime serviceOpenEndDt,
                                    LocalDateTime receiptStartDt, LocalDateTime receiptEndDt,
                                    LocalTime useTimeStart, LocalTime useTimeEnd,
                                    String cancelStdType, Short cancelStdDays) {
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
        this.lastSyncedAt = LocalDateTime.now();
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
}
