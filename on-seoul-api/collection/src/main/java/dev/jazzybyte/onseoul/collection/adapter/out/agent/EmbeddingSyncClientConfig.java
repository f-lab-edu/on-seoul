package dev.jazzybyte.onseoul.collection.adapter.out.agent;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

@Configuration
@EnableConfigurationProperties(EmbeddingSyncProperties.class)
class EmbeddingSyncClientConfig {

    @Bean("embeddingSyncWebClient")
    WebClient embeddingSyncWebClient(WebClient.Builder builder, EmbeddingSyncProperties properties) {
        return builder.baseUrl(properties.url()).build();
    }
}
