package dev.jazzybyte.onseoul.crypto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.ConstructorBinding;
import org.springframework.validation.annotation.Validated;

/**
 * AES-256-GCM 암호화 및 Blind Index 키 설정.
 * 환경변수 APP_AES_KEY, APP_BLIND_IDX_KEY 를 주입받는다.
 *
 * <p>{@code @Validated}가 적용되어 있으므로 키가 미설정되거나 형식이 올바르지 않으면
 * 애플리케이션 기동 시점에 {@link org.springframework.boot.context.properties.ConfigurationPropertiesBindException}이
 * 발생하며 명확한 검증 메시지를 출력한다.
 *
 * <p>키 포맷: AES-256 = 32바이트 = 64자리 16진수 소문자/대문자 허용.
 *
 * <p>키 값이 로그에 노출되지 않도록 {@link #toString()}을 오버라이드한다.
 */
@Validated
@ConfigurationProperties("app.encryption")
public class EncryptionProperties {

    /**
     * AES-256-GCM 대칭키. 32바이트(64 hex chars).
     * 환경변수 {@code APP_AES_KEY}.
     */
    @NotBlank(message = "app.encryption.aes-key: 환경변수 APP_AES_KEY가 미설정되었습니다")
    @Pattern(regexp = "[0-9a-fA-F]{64}",
             message = "app.encryption.aes-key: 64자리 16진수(AES-256-GCM = 32바이트)여야 합니다")
    private final String aesKey;

    /**
     * Blind Index HMAC-SHA256 키. 32바이트(64 hex chars).
     * 환경변수 {@code APP_BLIND_IDX_KEY}.
     */
    @NotBlank(message = "app.encryption.blind-idx-key: 환경변수 APP_BLIND_IDX_KEY가 미설정되었습니다")
    @Pattern(regexp = "[0-9a-fA-F]{64}",
             message = "app.encryption.blind-idx-key: 64자리 16진수(HMAC-SHA256 = 32바이트)여야 합니다")
    private final String blindIdxKey;

    @ConstructorBinding
    public EncryptionProperties(String aesKey, String blindIdxKey) {
        this.aesKey = aesKey;
        this.blindIdxKey = blindIdxKey;
    }

    public String getAesKey() {
        return aesKey;
    }

    public String getBlindIdxKey() {
        return blindIdxKey;
    }

    @Override
    public String toString() {
        return "EncryptionProperties{aesKey=[PROTECTED], blindIdxKey=[PROTECTED]}";
    }
}
