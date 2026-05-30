package dev.jazzybyte.onseoul.notification.adapter.out.agent;

import dev.jazzybyte.onseoul.notification.domain.NotificationTemplate;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.port.out.TemplateGenerationPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;

import java.time.Duration;

@Slf4j
@Component
class TemplateAgentClient implements TemplateGenerationPort {

    private final WebClient webClient;
    private final TemplateAgentDtoMapper mapper;
    private final TemplateAgentProperties properties;

    TemplateAgentClient(@Qualifier("templateAgentWebClient") WebClient webClient,
                        TemplateAgentDtoMapper mapper,
                        TemplateAgentProperties properties) {
        this.webClient = webClient;
        this.mapper = mapper;
        this.properties = properties;
    }

    @Override
    public TemplateResult generate(NotificationTemplateRequest request) {
        try {
            TemplateAgentResponse response = webClient.post()
                    .uri("/notification/template")
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(mapper.toRequest(request))
                    .retrieve()
                    .bodyToMono(TemplateAgentResponse.class)
                    .timeout(Duration.ofSeconds(properties.templateTimeoutSeconds()))
                    .block();

            if (response != null && response.isValid()) {
                log.debug("[TemplateAgent] AI 템플릿 생성 성공: serviceId={}", request.serviceId());
                return mapper.toDomain(response);
            }
            log.warn("[TemplateAgent] AI 응답 유효하지 않음(빈 title/body), fallback 사용: serviceId={}", request.serviceId());
        } catch (WebClientResponseException e) {
            log.warn("[TemplateAgent] AI 호출 non-2xx({}), fallback 사용: serviceId={}", e.getStatusCode(), request.serviceId());
        } catch (Exception e) {
            if (e.getCause() instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            log.warn("[TemplateAgent] AI 호출 실패({}), fallback 사용: serviceId={}", e.getClass().getSimpleName(), request.serviceId());
        }
        return NotificationTemplate.render(request);
    }
}
