package dev.jazzybyte.onseoul.util;

import lombok.extern.slf4j.Slf4j;

import java.math.BigDecimal;

/**
 * 서울시 Open API 숫자 문자열 파싱 유틸리티.
 */
@Slf4j
public final class NumberUtil {

    private NumberUtil() {}

    /**
     * 문자열을 {@link BigDecimal}로 파싱한다.
     *
     * @param value 파싱할 문자열 (null·blank·비숫자 → null 반환)
     */
    public static BigDecimal parseBigDecimal(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            return new BigDecimal(value.trim());
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /**
     * 문자열을 {@link Short}로 파싱한다.
     *
     * @param value     파싱할 문자열 (null·blank → null 반환)
     * @param fieldName 로그 식별용 필드명
     * @param svcid     로그 식별용 서비스 ID
     */
    public static Short parseShort(String value, String fieldName, String svcid) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            return Short.parseShort(value.trim());
        } catch (NumberFormatException e) {
            log.warn("숫자 파싱 실패 — svcid={}, field={}, value={}", svcid, fieldName, value);
            return null;
        }
    }
}
