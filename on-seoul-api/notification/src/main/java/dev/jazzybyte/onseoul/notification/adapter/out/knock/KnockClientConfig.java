package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.ClientRequest;
import org.springframework.web.reactive.function.client.ExchangeFilterFunction;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

@Configuration
@EnableConfigurationProperties(KnockProperties.class)
class KnockClientConfig {

    @Bean("knockWebClient")
    WebClient knockWebClient(WebClient.Builder builder, KnockProperties props) {
        return builder
                .baseUrl("https://api.knock.app")
                .defaultHeader("Content-Type", "application/json")
                .filter(authHeaderFilter(props.apiKey()))
                .build();
    }

    private static ExchangeFilterFunction authHeaderFilter(String apiKey) {
        return ExchangeFilterFunction.ofRequestProcessor(req ->
                Mono.just(ClientRequest.from(req)
                        .header("Authorization", "Bearer " + apiKey)
                        .build()));
    }
}
