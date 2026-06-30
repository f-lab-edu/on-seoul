package dev.jazzybyte.onseoul.collection.adapter.out.persistence.reservation;

import dev.jazzybyte.onseoul.util.HtmlTextUtil;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.CreationTimestamp;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.util.Objects;

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

    /**
     * 표시용 텍스트(+URL) 필드의 HTML 엔티티를 디코딩한다(일회성 백필용). 디코딩은 멱등이며,
     * change_log/알림을 유발하는 핵심 필드(serviceStatus/receipt*Dt) 비교와 무관하다.
     * serviceId/좌표/일시/cancelStdDays는 제외. lastSyncedAt은 갱신하지 않는다(의미상 동기화 아님).
     *
     * @return 어떤 값이라도 변경되었으면 true(멱등 — 이미 디코딩된 행은 false)
     */
    boolean decodeHtmlEntities() {
        String oldServiceGubun = serviceGubun;
        String oldMaxClassName = maxClassName;
        String oldMinClassName = minClassName;
        String oldServiceName = serviceName;
        String oldServiceStatus = serviceStatus;
        String oldPaymentType = paymentType;
        String oldTargetInfo = targetInfo;
        String oldServiceUrl = serviceUrl;
        String oldImageUrl = imageUrl;
        String oldDetailContent = detailContent;
        String oldTelNo = telNo;
        String oldPlaceName = placeName;
        String oldAreaName = areaName;
        String oldCancelStdType = cancelStdType;

        this.serviceGubun = HtmlTextUtil.unescape(serviceGubun);
        this.maxClassName = HtmlTextUtil.unescape(maxClassName);
        this.minClassName = HtmlTextUtil.unescape(minClassName);
        this.serviceName = HtmlTextUtil.unescape(serviceName);
        this.serviceStatus = HtmlTextUtil.unescape(serviceStatus);
        this.paymentType = HtmlTextUtil.unescape(paymentType);
        this.targetInfo = HtmlTextUtil.unescape(targetInfo);
        this.serviceUrl = HtmlTextUtil.unescape(serviceUrl);
        this.imageUrl = HtmlTextUtil.unescape(imageUrl);
        this.detailContent = HtmlTextUtil.unescape(detailContent);
        this.telNo = HtmlTextUtil.unescape(telNo);
        this.placeName = HtmlTextUtil.unescape(placeName);
        this.areaName = HtmlTextUtil.unescape(areaName);
        this.cancelStdType = HtmlTextUtil.unescape(cancelStdType);

        return !Objects.equals(oldServiceGubun, serviceGubun)
                || !Objects.equals(oldMaxClassName, maxClassName)
                || !Objects.equals(oldMinClassName, minClassName)
                || !Objects.equals(oldServiceName, serviceName)
                || !Objects.equals(oldServiceStatus, serviceStatus)
                || !Objects.equals(oldPaymentType, paymentType)
                || !Objects.equals(oldTargetInfo, targetInfo)
                || !Objects.equals(oldServiceUrl, serviceUrl)
                || !Objects.equals(oldImageUrl, imageUrl)
                || !Objects.equals(oldDetailContent, detailContent)
                || !Objects.equals(oldTelNo, telNo)
                || !Objects.equals(oldPlaceName, placeName)
                || !Objects.equals(oldAreaName, areaName)
                || !Objects.equals(oldCancelStdType, cancelStdType);
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
