package dev.jazzybyte.onseoul.notification.adapter.out.fcm;

import com.google.auth.oauth2.GoogleCredentials;
import com.google.firebase.FirebaseApp;
import com.google.firebase.FirebaseOptions;
import com.google.firebase.messaging.FirebaseMessaging;
import jakarta.annotation.PostConstruct;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.FileSystemResource;
import org.springframework.util.StringUtils;

import java.io.IOException;
import java.io.InputStream;

@Slf4j
@Configuration
@EnableConfigurationProperties(FcmProperties.class)
class FcmConfig {

    private final FcmProperties properties;
    private FirebaseMessaging firebaseMessaging;

    FcmConfig(FcmProperties properties) {
        this.properties = properties;
    }

    @PostConstruct
    void init() {
        if (!StringUtils.hasText(properties.serviceAccountPath())) {
            log.warn("[FCM] app.fcm.service-account-path 미설정 — FCM 푸시 비활성화");
            return;
        }
        if (!FirebaseApp.getApps().isEmpty()) {
            this.firebaseMessaging = FirebaseMessaging.getInstance();
            return;
        }
        try (InputStream is = new FileSystemResource(properties.serviceAccountPath()).getInputStream()) {
            FirebaseOptions options = FirebaseOptions.builder()
                    .setCredentials(GoogleCredentials.fromStream(is))
                    .build();
            FirebaseApp.initializeApp(options);
            this.firebaseMessaging = FirebaseMessaging.getInstance();
            log.info("[FCM] Firebase Admin 초기화 완료");
        } catch (IOException e) {
            throw new IllegalStateException("[FCM] Firebase Admin 초기화 실패: " + properties.serviceAccountPath(), e);
        }
    }

    @Bean
    FirebaseMessaging firebaseMessaging() {
        return firebaseMessaging;
    }
}
