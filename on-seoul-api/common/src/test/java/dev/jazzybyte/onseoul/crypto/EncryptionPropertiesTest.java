package dev.jazzybyte.onseoul.crypto;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * EncryptionProperties 보안 특성 검증.
 * 키 값이 toString()을 통해 로그에 노출되지 않는지 확인한다.
 */
class EncryptionPropertiesTest {

    private static final String AES_KEY = "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20";
    private static final String BLIND_KEY = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2";

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
}
