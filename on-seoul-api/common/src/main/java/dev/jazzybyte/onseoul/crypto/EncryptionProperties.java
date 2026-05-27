package dev.jazzybyte.onseoul.crypto;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.ConstructorBinding;

/**
 * AES-256-GCM 암호화 및 Blind Index 키 설정.
 * 환경변수 APP_AES_KEY, APP_BLIND_IDX_KEY 를 주입받는다.
 * 키 값이 로그에 노출되지 않도록 toString()을 직접 오버라이드한다.
 */
@ConfigurationProperties("app.encryption")
public class EncryptionProperties {

    private final String aesKey;

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
