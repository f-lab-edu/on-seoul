package dev.jazzybyte.onseoul.adapter.out.aiservice;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "ai.service")
public record AiServiceProperties(String url, int streamTimeoutSeconds) {}
