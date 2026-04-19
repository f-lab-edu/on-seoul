package dev.jazzybyte.onseoul.util;

import lombok.extern.slf4j.Slf4j;

import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeFormatterBuilder;
import java.time.temporal.ChronoField;

/**
 * 서울시 Open API 날짜/시간 문자열 파싱 유틸리티.
 */
@Slf4j
public final class DateUtil {

    /** "yyyy-MM-dd HH:mm:ss" + 선택적 소수점 초 ({@code .0} 등) */
    private static final DateTimeFormatter DATE_TIME_FORMATTER = new DateTimeFormatterBuilder()
            .appendPattern("yyyy-MM-dd HH:mm:ss")
            .optionalStart()
            .appendFraction(ChronoField.NANO_OF_SECOND, 0, 9, true)
            .optionalEnd()
            .toFormatter();

    private DateUtil() {}

    /**
     * 날짜 문자열을 {@link LocalDateTime}으로 파싱한다.
     *
     * @param value     파싱할 문자열 (null·blank → null 반환)
     * @param fieldName 로그 식별용 필드명
     * @param svcid     로그 식별용 서비스 ID
     */
    public static LocalDateTime parseDateTime(String value, String fieldName, String svcid) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            return LocalDateTime.parse(value.trim(), DATE_TIME_FORMATTER);
        } catch (Exception e) {
            log.warn("날짜 파싱 실패 — svcid={}, field={}, value={}", svcid, fieldName, value);
            return null;
        }
    }

    /**
     * "HH:mm" 포맷의 시간 문자열을 {@link LocalTime}으로 파싱한다.
     *
     * <p>서울시 API 일부 데이터에는 {@code "30:00"}, {@code "33:00"} 처럼 24를 초과하는
     * 시간 값이 포함된다. 이 경우 {@code hour % 24}로 정규화하여 반환하고 WARN 로그를 남긴다.</p>
     *
     * @param value     파싱할 문자열 (null·blank → null 반환)
     * @param fieldName 로그 식별용 필드명
     * @param svcid     로그 식별용 서비스 ID
     */
    public static LocalTime parseTime(String value, String fieldName, String svcid) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            String[] parts = value.trim().split(":");
            if (parts.length != 2) {
                log.warn("시간 파싱 실패 — svcid={}, field={}, value={}", svcid, fieldName, value);
                return null;
            }
            int hour = Integer.parseInt(parts[0]);
            int minute = Integer.parseInt(parts[1]);
            if (hour >= 24) {
                int normalizedHour = hour % 24;
                log.warn("24시 초과 시간값 정규화 — svcid={}, field={}, value={} → {:02d}:{}",
                        svcid, fieldName, value, normalizedHour, parts[1]);
                hour = normalizedHour;
            }
            return LocalTime.of(hour, minute);
        } catch (Exception e) {
            log.warn("시간 파싱 실패 — svcid={}, field={}, value={}", svcid, fieldName, value);
            return null;
        }
    }
}
