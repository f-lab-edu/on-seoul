package dev.jazzybyte.onseoul.crypto;

import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.nio.ByteBuffer;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.HexFormat;

/**
 * AES-256-GCM 암호화/복호화 컴포넌트.
 *
 * <p>출력 형식: {@code v1:<base64(nonce[12] + ciphertext + tag[16])>}
 *
 * <p>AAD로 {@code userId}(Long → 8 bytes)를 사용하여 Copy Attack을 방어한다.
 * 복호화 시 동일한 userId를 전달하지 않으면 {@link javax.crypto.AEADBadTagException}이 발생한다.
 */
public class AesGcmEncryptor {

    private static final String ALGORITHM = "AES/GCM/NoPadding";
    private static final String VERSION_PREFIX = "v1:";
    private static final int NONCE_BYTES = 12;
    private static final int TAG_BITS = 128;

    private final SecretKey secretKey;
    private final SecureRandom secureRandom = new SecureRandom();

    public AesGcmEncryptor(String aesKeyHex) {
        byte[] keyBytes = HexFormat.of().parseHex(aesKeyHex);
        if (keyBytes.length != 32) {
            throw new IllegalArgumentException("AES key must be 32 bytes (256 bit)");
        }
        this.secretKey = new SecretKeySpec(keyBytes, "AES");
    }

    /**
     * 평문을 암호화한다. null 입력 시 null을 반환한다.
     *
     * @param plaintext 평문 (nullable)
     * @param userId    AAD로 사용할 사용자 ID
     * @return {@code v1:<base64(nonce[12] + ciphertext + tag[16])>} 형식의 암호문
     * @throws CryptoException 암호화 실패 시
     */
    public String encrypt(String plaintext, long userId) {
        if (plaintext == null) {
            return null;
        }
        try {
            byte[] nonce = new byte[NONCE_BYTES];
            secureRandom.nextBytes(nonce);

            Cipher cipher = Cipher.getInstance(ALGORITHM);
            cipher.init(Cipher.ENCRYPT_MODE, secretKey, new GCMParameterSpec(TAG_BITS, nonce));
            cipher.updateAAD(longToBytes(userId));

            byte[] ciphertextWithTag = cipher.doFinal(plaintext.getBytes(java.nio.charset.StandardCharsets.UTF_8));

            // nonce(12) + ciphertext+tag
            byte[] payload = ByteBuffer.allocate(nonce.length + ciphertextWithTag.length)
                    .put(nonce)
                    .put(ciphertextWithTag)
                    .array();

            return VERSION_PREFIX + Base64.getEncoder().encodeToString(payload);
        } catch (Exception e) {
            throw new CryptoException("Encryption failed", e);
        }
    }

    /**
     * 암호문을 복호화한다. null 입력 시 null을 반환한다.
     *
     * @param ciphertext {@code v1:<base64>} 형식의 암호문 (nullable)
     * @param userId     AAD로 사용할 사용자 ID (encrypt 시와 동일해야 함)
     * @return 복호화된 평문
     * @throws IllegalArgumentException 버전 접두사가 맞지 않을 때
     * @throws CryptoException          복호화 실패 (cause: {@link javax.crypto.AEADBadTagException})
     */
    public String decrypt(String ciphertext, long userId) {
        if (ciphertext == null) {
            return null;
        }
        if (!ciphertext.startsWith(VERSION_PREFIX)) {
            throw new IllegalArgumentException("Unsupported ciphertext version prefix");
        }
        try {
            byte[] payload = Base64.getDecoder().decode(ciphertext.substring(VERSION_PREFIX.length()));

            ByteBuffer buf = ByteBuffer.wrap(payload);
            byte[] nonce = new byte[NONCE_BYTES];
            buf.get(nonce);
            byte[] ciphertextWithTag = new byte[buf.remaining()];
            buf.get(ciphertextWithTag);

            Cipher cipher = Cipher.getInstance(ALGORITHM);
            cipher.init(Cipher.DECRYPT_MODE, secretKey, new GCMParameterSpec(TAG_BITS, nonce));
            cipher.updateAAD(longToBytes(userId));

            byte[] plainBytes = cipher.doFinal(ciphertextWithTag);
            return new String(plainBytes, java.nio.charset.StandardCharsets.UTF_8);
        } catch (javax.crypto.AEADBadTagException e) {
            throw new CryptoException("Decryption failed: AAD mismatch or tampered ciphertext", e);
        } catch (Exception e) {
            throw new CryptoException("Decryption failed", e);
        }
    }

    private static byte[] longToBytes(long value) {
        return ByteBuffer.allocate(Long.BYTES).putLong(value).array();
    }
}
