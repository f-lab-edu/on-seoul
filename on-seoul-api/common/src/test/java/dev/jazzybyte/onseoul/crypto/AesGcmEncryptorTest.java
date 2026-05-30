package dev.jazzybyte.onseoul.crypto;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import javax.crypto.AEADBadTagException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class AesGcmEncryptorTest {

    // 32 바이트 hex (64자)
    private static final String AES_KEY_HEX = "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20";
    private AesGcmEncryptor encryptor;

    @BeforeEach
    void setUp() {
        encryptor = new AesGcmEncryptor(AES_KEY_HEX);
    }

    @Test
    @DisplayName("encrypt() 결과가 v1: 접두사를 포함한다")
    void encrypt_returnsV1Prefix() {
        String ciphertext = encryptor.encrypt("hello@example.com", 1L);
        assertThat(ciphertext).startsWith("v1:");
    }

    @Test
    @DisplayName("encrypt → decrypt 라운드트립 — 원문 복원")
    void roundTrip_restoresPlaintext() {
        String plaintext = "user@seoul.go.kr";
        Long userId = 42L;

        String encrypted = encryptor.encrypt(plaintext, userId);
        String decrypted = encryptor.decrypt(encrypted, userId);

        assertThat(decrypted).isEqualTo(plaintext);
    }

    @Test
    @DisplayName("encrypt()는 같은 입력에도 매번 다른 암호문(nonce 랜덤)")
    void encrypt_sameInput_producesDistinctCiphertexts() {
        String plaintext = "same@email.com";
        Long userId = 1L;

        String c1 = encryptor.encrypt(plaintext, userId);
        String c2 = encryptor.encrypt(plaintext, userId);

        assertThat(c1).isNotEqualTo(c2);
    }

    @Test
    @DisplayName("AAD 불일치(다른 userId)로 복호화 시 AEADBadTagException")
    void decrypt_wrongUserId_throwsAEADBadTagException() {
        String encrypted = encryptor.encrypt("secret@email.com", 1L);

        assertThatThrownBy(() -> encryptor.decrypt(encrypted, 999L))
                .hasCauseInstanceOf(AEADBadTagException.class);
    }

    @Test
    @DisplayName("null 입력 → null 반환 (encrypt)")
    void encrypt_null_returnsNull() {
        assertThat(encryptor.encrypt(null, 1L)).isNull();
    }

    @Test
    @DisplayName("null 입력 → null 반환 (decrypt)")
    void decrypt_null_returnsNull() {
        assertThat(encryptor.decrypt(null, 1L)).isNull();
    }

    @Test
    @DisplayName("잘못된 v1 prefix가 없는 문자열 복호화 시 예외")
    void decrypt_invalidPrefix_throwsException() {
        assertThatThrownBy(() -> encryptor.decrypt("v2:AAAA", 1L))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Unsupported ciphertext version");
    }

    @Test
    @DisplayName("키 길이 오류(32바이트 미만) → IllegalArgumentException")
    void constructor_shortKey_throwsIllegalArgumentException() {
        // 16바이트 hex (32자) — AES-128이지만 이 구현은 256-bit 전용
        String shortKeyHex = "0102030405060708090a0b0c0d0e0f10";
        assertThatThrownBy(() -> new AesGcmEncryptor(shortKeyHex))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("32 bytes");
    }

    @Test
    @DisplayName("키 길이 오류(32바이트 초과) → IllegalArgumentException")
    void constructor_longKey_throwsIllegalArgumentException() {
        // 40바이트 hex (80자)
        String longKeyHex = "0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f2021222324";
        assertThatThrownBy(() -> new AesGcmEncryptor(longKeyHex))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("32 bytes");
    }
}
