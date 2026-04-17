package dev.jazzybyte.onseoul.collector;

import dev.jazzybyte.onseoul.collector.dto.PublicServiceRow;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import dev.jazzybyte.onseoul.util.DateUtil;
import dev.jazzybyte.onseoul.util.NumberUtil;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.Optional;

/**
 * 서울시 Open API 응답 row({@link PublicServiceRow})를
 * JPA 엔티티({@link PublicServiceReservation})로 변환한다.
 *
 * <p><b>필드 처리 정책:</b></p>
 * <ul>
 *   <li><b>필수 필드</b>({@code SVCID}, {@code SVCNM}): null·blank 시 해당 row를 스킵하고 WARN 로그.
 *       {@code Optional.empty()} 반환.</li>
 *   <li><b>선택 필드</b>: 변환 실패 시 {@code null}로 처리하고 WARN 로그.</li>
 *   <li>날짜/시간/숫자 파싱: {@link DateUtil}, {@link NumberUtil} 위임</li>
 *   <li>좌표: 빈 문자열·null → {@code null} (Geocoding fallback 대상)</li>
 * </ul>
 */
@Slf4j
@Component
public class PublicServiceRowMapper {

    /**
     * API 응답 row를 엔티티로 변환한다.
     *
     * @return 변환된 엔티티. 필수 필드({@code SVCID}, {@code SVCNM})가 없으면 {@code Optional.empty()}
     */
    public Optional<PublicServiceReservation> toEntity(PublicServiceRow row) {
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
