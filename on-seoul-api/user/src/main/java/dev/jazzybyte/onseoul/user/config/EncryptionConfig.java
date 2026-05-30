package dev.jazzybyte.onseoul.user.config;

import dev.jazzybyte.onseoul.crypto.AesGcmEncryptor;
import dev.jazzybyte.onseoul.crypto.BlindIndexer;
import dev.jazzybyte.onseoul.crypto.EncryptionProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(EncryptionProperties.class)
public class EncryptionConfig {

    @Bean
    public AesGcmEncryptor aesGcmEncryptor(EncryptionProperties props) {
        return new AesGcmEncryptor(props.getAesKey());
    }

    @Bean
    public BlindIndexer blindIndexer(EncryptionProperties props) {
        return new BlindIndexer(props.getBlindIdxKey());
    }
}
