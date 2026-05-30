package dev.jazzybyte.onseoul.crypto;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(EncryptionProperties.class)
public class CommonEncryptionConfig {

    @Bean
    public AesGcmEncryptor aesGcmEncryptor(EncryptionProperties props) {
        return new AesGcmEncryptor(props.getAesKey());
    }

    @Bean
    public BlindIndexer blindIndexer(EncryptionProperties props) {
        return new BlindIndexer(props.getBlindIdxKey());
    }
}
