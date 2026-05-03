package dev.jazzybyte.onseoul.adapter.out.aiservice;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

@Configuration
@EnableConfigurationProperties(AiServiceProperties.class)
public class AiServiceClientConfig {

    private final AiServiceProperties properties;

    public AiServiceClientConfig(final AiServiceProperties properties) {
        this.properties = properties;
    }

    @Bean("aiServiceWebClient")
    public WebClient aiServiceWebClient(WebClient.Builder builder) {
        return builder
                .baseUrl(properties.url())
                .build();
    }
}
