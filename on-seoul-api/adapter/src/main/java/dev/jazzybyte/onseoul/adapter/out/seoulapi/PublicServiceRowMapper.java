package dev.jazzybyte.onseoul.adapter.out.seoulapi;

import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;
import dev.jazzybyte.onseoul.util.DateUtil;
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

        return Optional.of(PublicServiceReservation.builder()
                .serviceId(svcid)
                .serviceGubun(row.getGubun())
                .maxClassName(row.getMaxclassnm())
                .minClassName(row.getMinclassnm())
                .serviceName(svcnm)
                .serviceStatus(row.getSvcstatnm())
                .paymentType(row.getPayatnm())
                .targetInfo(trimToNull(row.getUsetgtinfo()))
                .serviceUrl(trimToNull(row.getSvcurl()))
                .imageUrl(trimToNull(row.getImgurl()))
                .detailContent(row.getDtlcont())
                .telNo(trimToNull(row.getTelno()))
                .placeName(row.getPlacenm())
                .areaName(row.getAreanm())
                .coordX(NumberUtil.parseBigDecimal(row.getX()))
                .coordY(NumberUtil.parseBigDecimal(row.getY()))
                .serviceOpenStartDt(DateUtil.parseDateTime(row.getSvcopnbgndt(), "SVCOPNBGNDT", svcid))
                .serviceOpenEndDt(DateUtil.parseDateTime(row.getSvcopnenddt(), "SVCOPNENDDT", svcid))
                .receiptStartDt(DateUtil.parseDateTime(row.getRcptbgndt(), "RCPTBGNDT", svcid))
                .receiptEndDt(DateUtil.parseDateTime(row.getRcptenddt(), "RCPTENDDT", svcid))
                .useTimeStart(DateUtil.parseTime(row.getVMin(), "V_MIN", svcid))
                .useTimeEnd(DateUtil.parseTime(row.getVMax(), "V_MAX", svcid))
                .cancelStdType(trimToNull(row.getRevstddaynm()))
                .cancelStdDays(NumberUtil.parseShort(row.getRevstdday(), "REVSTDDAY", svcid))
                .build());
    }

    private String trimToNull(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        return value.trim();
    }
}
