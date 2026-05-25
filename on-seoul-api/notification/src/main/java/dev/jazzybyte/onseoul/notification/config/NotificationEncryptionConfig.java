package dev.jazzybyte.onseoul.notification.config;

import dev.jazzybyte.onseoul.crypto.AesGcmEncryptor;
import dev.jazzybyte.onseoul.crypto.EncryptionProperties;
import org.springframework.boot.autoconfigure.condition.ConditionalOnMissingBean;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * notification BC에서 사용하는 AES-GCM 복호화 빈 등록.
 * UserContactPersistenceAdapter가 users 테이블의 암호화 컬럼을 복호화할 때 사용한다.
 *
 * <p>user BC와 동일한 컨텍스트에서 실행될 때 user.config.EncryptionConfig가 이미
 * AesGcmEncryptor를 등록하므로 {@link ConditionalOnMissingBean}으로 중복 등록을 방지한다.
 */
@Configuration
@EnableConfigurationProperties(EncryptionProperties.class)
public class NotificationEncryptionConfig {

    @Bean
    @ConditionalOnMissingBean(AesGcmEncryptor.class)
    public AesGcmEncryptor aesGcmEncryptor(EncryptionProperties props) {
        return new AesGcmEncryptor(props.getAesKey());
    }
}
