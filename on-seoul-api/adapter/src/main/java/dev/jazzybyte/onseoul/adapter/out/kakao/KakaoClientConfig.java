package dev.jazzybyte.onseoul.adapter.out.kakao;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

@Configuration
@EnableConfigurationProperties(KakaoApiProperties.class)
class KakaoClientConfig {

    @Bean
    public WebClient kakaoWebClient(KakaoApiProperties properties) {
        return WebClient.builder()
                .baseUrl(properties.getBaseUrl())
                .defaultHeader("Authorization", "KakaoAK " + properties.getKey())
                .build();
    }
}
