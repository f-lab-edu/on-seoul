package dev.jazzybyte.onseoul.crypto;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import jakarta.validation.ValidatorFactory;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.NullAndEmptySource;
import org.junit.jupiter.params.provider.ValueSource;

import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * EncryptionProperties 보안 특성 및 Bean Validation 검증.
 * - 키 값이 toString()을 통해 로그에 노출되지 않는지 확인한다.
 * - null/빈값/형식 오류 시 명확한 검증 메시지가 발생하는지 확인한다.
 */
class EncryptionPropertiesTest {

    private static final String AES_KEY = "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20";
    private static final String BLIND_KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2";

    private static Validator validator;

    @BeforeAll
    static void setUpValidator() {
        try (ValidatorFactory factory = Validation.buildDefaultValidatorFactory()) {
            validator = factory.getValidator();
        }
    }

    // ── toString / getter ────────────────────────────────────────────────

    @Test
    @DisplayName("toString()이 키 값을 노출하지 않는다 — [PROTECTED] 마스킹")
    void toString_doesNotExposeKeys() {
        EncryptionProperties props = new EncryptionProperties(AES_KEY, BLIND_KEY);
        String result = props.toString();

        assertThat(result).doesNotContain(AES_KEY);
        assertThat(result).doesNotContain(BLIND_KEY);
        assertThat(result).contains("[PROTECTED]");
    }

    @Test
    @DisplayName("getAesKey() / getBlindIdxKey() 는 실제 키 값을 반환한다")
    void getters_returnRawKeyValues() {
        EncryptionProperties props = new EncryptionProperties(AES_KEY, BLIND_KEY);

        assertThat(props.getAesKey()).isEqualTo(AES_KEY);
        assertThat(props.getBlindIdxKey()).isEqualTo(BLIND_KEY);
    }

    // ── Bean Validation: 정상 케이스 ─────────────────────────────────────

    @Test
    @DisplayName("유효한 64자리 16진수 키 — 검증 위반 없음")
    void validation_validKeys_noViolations() {
        EncryptionProperties props = new EncryptionProperties(AES_KEY, BLIND_KEY);

        Set<ConstraintViolation<EncryptionProperties>> violations = validator.validate(props);

        assertThat(violations).isEmpty();
    }

    // ── Bean Validation: aesKey 오류 케이스 ─────────────────────────────

    @ParameterizedTest(name = "aesKey={0}")
    @NullAndEmptySource
    @DisplayName("aesKey null/빈값 — @NotBlank 위반, 명확한 메시지 출력")
    void validation_nullOrEmptyAesKey_notBlankViolation(String invalidKey) {
        EncryptionProperties props = new EncryptionProperties(invalidKey, BLIND_KEY);

        Set<ConstraintViolation<EncryptionProperties>> violations = validator.validate(props);

        assertThat(violations).isNotEmpty();
        assertThat(violations).anyMatch(v ->
                v.getPropertyPath().toString().equals("aesKey")
                && v.getMessage().contains("APP_AES_KEY"));
    }

    @ParameterizedTest(name = "aesKey={0}")
    @ValueSource(strings = {
            "too-short",                                              // 길이 부족
            "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20FF", // 65자 (초과)
            "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ",   // 비16진수
            "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f2",    // 63자 (1 부족)
    })
    @DisplayName("aesKey 형식 오류 — @Pattern 위반, 명확한 메시지 출력")
    void validation_invalidFormatAesKey_patternViolation(String invalidKey) {
        EncryptionProperties props = new EncryptionProperties(invalidKey, BLIND_KEY);

        Set<ConstraintViolation<EncryptionProperties>> violations = validator.validate(props);

        assertThat(violations).isNotEmpty();
        assertThat(violations).anyMatch(v ->
                v.getPropertyPath().toString().equals("aesKey")
                && v.getMessage().contains("64자리 16진수"));
    }

    // ── Bean Validation: blindIdxKey 오류 케이스 ─────────────────────────

    @ParameterizedTest(name = "blindIdxKey={0}")
    @NullAndEmptySource
    @DisplayName("blindIdxKey null/빈값 — @NotBlank 위반, 명확한 메시지 출력")
    void validation_nullOrEmptyBlindIdxKey_notBlankViolation(String invalidKey) {
        EncryptionProperties props = new EncryptionProperties(AES_KEY, invalidKey);

        Set<ConstraintViolation<EncryptionProperties>> violations = validator.validate(props);

        assertThat(violations).isNotEmpty();
        assertThat(violations).anyMatch(v ->
                v.getPropertyPath().toString().equals("blindIdxKey")
                && v.getMessage().contains("APP_BLIND_IDX_KEY"));
    }

    @Test
    @DisplayName("blindIdxKey 형식 오류 — @Pattern 위반, 명확한 메시지 출력")
    void validation_invalidFormatBlindIdxKey_patternViolation() {
        EncryptionProperties props = new EncryptionProperties(AES_KEY, "not-hex-at-all");

        Set<ConstraintViolation<EncryptionProperties>> violations = validator.validate(props);

        assertThat(violations).isNotEmpty();
        assertThat(violations).anyMatch(v ->
                v.getPropertyPath().toString().equals("blindIdxKey")
                && v.getMessage().contains("64자리 16진수"));
    }
}
