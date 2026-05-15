package dev.jazzybyte.onseoul.notification.adapter.out.fcm;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "app.fcm")
record FcmProperties(String serviceAccountPath) {}
