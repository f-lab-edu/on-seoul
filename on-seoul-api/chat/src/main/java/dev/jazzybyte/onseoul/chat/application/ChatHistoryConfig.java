package dev.jazzybyte.onseoul.chat.application;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties(ChatHistoryProperties.class)
public class ChatHistoryConfig {
}
