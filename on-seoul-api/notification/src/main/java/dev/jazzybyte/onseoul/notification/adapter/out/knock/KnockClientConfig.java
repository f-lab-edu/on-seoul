package dev.jazzybyte.onseoul.notification.adapter.out.knock;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.reactive.ReactorClientHttpConnector;
import org.springframework.web.reactive.function.client.ClientRequest;
import org.springframework.web.reactive.function.client.ExchangeFilterFunction;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;
import reactor.netty.http.client.HttpClient;

@Configuration
@EnableConfigurationProperties(KnockProperties.class)
class KnockClientConfig {

    /**
     * Knock API용 WebClient.
     *
     * <p><b>PII 보호 — wiretap 명시 비활성화:</b>
     * 워크플로우 트리거 요청 본문({@code recipients})에 email/phone_number가 평문으로 포함된다.
     * reactor-netty {@code wiretap}을 명시적으로 {@code false}로 고정하여
     * HTTP body 로깅 경로를 차단한다.
     *
     * <ul>
     *   <li>이 값을 {@code true}로 변경하면 요청 본문 전체가 DEBUG 로그로 출력된다 → PII 노출.</li>
     *   <li>APM 에이전트의 HTTP body 캡처({@code opentelemetry.instrumentation.http.capture-headers})도
     *       비활성화 상태를 유지해야 한다.</li>
     * </ul>
     *
     * <p>향후 Knock User API({@code PUT /v1/users/{id}})로 수신자를 사전 등록하고
     * 트리거 시 id만 전달하는 방식으로 전환하면 본문에서 PII를 완전히 제거할 수 있다.
     */
    @Bean("knockWebClient")
    WebClient knockWebClient(WebClient.Builder builder, KnockProperties props) {
        // wiretap=false 명시: reactor-netty HTTP body 로깅 경로를 코드 레벨에서 차단.
        // 기본값도 false이나, 보안 의도를 명확히 하고 미래 변경 시 코드리뷰에서 감지되도록 명시한다.
        HttpClient httpClient = HttpClient.create().wiretap(false);

        return builder
                .baseUrl("https://api.knock.app")
                .defaultHeader("Content-Type", "application/json")
                .clientConnector(new ReactorClientHttpConnector(httpClient))
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
