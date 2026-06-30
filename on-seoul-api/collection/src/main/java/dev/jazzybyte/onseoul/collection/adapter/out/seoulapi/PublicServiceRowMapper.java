package dev.jazzybyte.onseoul.collection.adapter.out.seoulapi;

import dev.jazzybyte.onseoul.collection.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.util.DateUtil;
import dev.jazzybyte.onseoul.util.HtmlTextUtil;
import dev.jazzybyte.onseoul.util.NumberUtil;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.Optional;

@Slf4j
@Component
class PublicServiceRowMapper {

    Optional<PublicServiceReservation> toEntity(PublicServiceRow row) {
        String svcid = row.getSvcid();
        String svcnm = row.getSvcnm();

        if (svcid == null || svcid.isBlank()) {
            log.warn("필수 필드 누락으로 row 스킵 — field=SVCID");
            return Optional.empty();
        }
        if (svcnm == null || svcnm.isBlank()) {
            log.warn("필수 필드 누락으로 row 스킵 — svcid={}, field=SVCNM", svcid);
            return Optional.empty();
        }

        // 표시용 텍스트(+URL)는 저장 경계에서 HTML 엔티티를 디코딩한다. serviceId/좌표/일시 등은 제외.
        // 디코딩은 HtmlUtils 1회만(멱등). trimToNull 대상은 디코딩 → trim 순서.
        return Optional.of(PublicServiceReservation.builder()
                .serviceId(svcid)
                .serviceGubun(decode(row.getGubun()))
                .maxClassName(decode(row.getMaxclassnm()))
                .minClassName(decode(row.getMinclassnm()))
                .serviceName(decode(svcnm))
                .serviceStatus(decode(row.getSvcstatnm()))
                .paymentType(decode(row.getPayatnm()))
                .targetInfo(decodeTrimToNull(row.getUsetgtinfo()))
                .serviceUrl(decodeTrimToNull(row.getSvcurl()))
                .imageUrl(decodeTrimToNull(row.getImgurl()))
                .detailContent(decode(row.getDtlcont()))
                .telNo(decodeTrimToNull(row.getTelno()))
                .placeName(decode(row.getPlacenm()))
                .areaName(decode(row.getAreanm()))
                .coordX(NumberUtil.parseBigDecimal(row.getX()))
                .coordY(NumberUtil.parseBigDecimal(row.getY()))
                .serviceOpenStartDt(DateUtil.parseDateTime(row.getSvcopnbgndt(), "SVCOPNBGNDT", svcid))
                .serviceOpenEndDt(DateUtil.parseDateTime(row.getSvcopnenddt(), "SVCOPNENDDT", svcid))
                .receiptStartDt(DateUtil.parseDateTime(row.getRcptbgndt(), "RCPTBGNDT", svcid))
                .receiptEndDt(DateUtil.parseDateTime(row.getRcptenddt(), "RCPTENDDT", svcid))
                .useTimeStart(DateUtil.parseTime(row.getVMin(), "V_MIN", svcid))
                .useTimeEnd(DateUtil.parseTime(row.getVMax(), "V_MAX", svcid))
                .cancelStdType(decodeTrimToNull(row.getRevstddaynm()))
                .cancelStdDays(NumberUtil.parseShort(row.getRevstdday(), "REVSTDDAY", svcid))
                .build());
    }

    /** HTML 엔티티 디코딩(멱등). */
    private String decode(String value) {
        return HtmlTextUtil.unescape(value);
    }

    /** 디코딩 후 trim, 공백/빈 값은 null. */
    private String decodeTrimToNull(String value) {
        return trimToNull(HtmlTextUtil.unescape(value));
    }

    private String trimToNull(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return value.trim();
    }
}
