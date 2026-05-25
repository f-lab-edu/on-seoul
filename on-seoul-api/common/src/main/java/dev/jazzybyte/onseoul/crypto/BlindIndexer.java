package dev.jazzybyte.onseoul.crypto;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.util.HexFormat;
import java.util.Locale;

/**
 * HMAC-SHA256 기반 Blind Index 생성 컴포넌트.
 *
 * <p>도메인 분리자를 포함하여 {@code "users||email||<normalized>"} 형태로
 * HMAC 입력을 구성하므로, 동일한 값이라도 타입(email vs phone)에 따라 다른 결과가 나온다.
 *
 * <p>정규화 규칙:
 * <ul>
 *   <li>email: {@code toLowerCase(Locale.ROOT).trim()}</li>
 *   <li>phone: 숫자와 {@code +}만 남김</li>
 * </ul>
 */
public class BlindIndexer {

    private static final String ALGORITHM = "HmacSHA256";
    private static final String DOMAIN = "users";

    private final SecretKeySpec secretKeySpec;

    public BlindIndexer(String blindIdxKeyHex) {
        byte[] keyBytes = HexFormat.of().parseHex(blindIdxKeyHex);
        if (keyBytes.length != 32) {
            throw new IllegalArgumentException("Blind index key must be 32 bytes (256 bit)");
        }
        this.secretKeySpec = new SecretKeySpec(keyBytes, ALGORITHM);
    }

    /**
     * 입력 값에 대한 Blind Index를 반환한다. null 입력 시 null을 반환한다.
     *
     * @param value 인덱싱할 원문 값 (nullable)
     * @param type  "email" 또는 "phone" (도메인 분리자)
     * @return 64자 소문자 hex 문자열
     */
    public String index(String value, String type) {
        if (value == null) {
            return null;
        }
        String normalized = normalize(value, type);
        String input = DOMAIN + "||" + type + "||" + normalized;
        try {
            Mac mac = Mac.getInstance(ALGORITHM);
            mac.init(this.secretKeySpec);
            byte[] hmac = mac.doFinal(input.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(hmac);
        } catch (Exception e) {
            throw new RuntimeException("Blind index computation failed", e);
        }
    }

    private String normalize(String value, String type) {
        return switch (type) {
            case "email" -> value.toLowerCase(Locale.ROOT).trim();
            case "phone" -> value.replaceAll("[^0-9+]", "");
            default -> throw new IllegalArgumentException("Unknown type: " + type);
        };
    }
}
