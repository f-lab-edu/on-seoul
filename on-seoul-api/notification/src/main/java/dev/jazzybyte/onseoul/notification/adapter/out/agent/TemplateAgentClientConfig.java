package dev.jazzybyte.onseoul.notification.adapter.out.agent;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

@Configuration
@EnableConfigurationProperties(TemplateAgentProperties.class)
class TemplateAgentClientConfig {

    @Bean("templateAgentWebClient")
    WebClient templateAgentWebClient(WebClient.Builder builder, TemplateAgentProperties properties) {
        return builder.baseUrl(properties.url()).build();
    }
}
