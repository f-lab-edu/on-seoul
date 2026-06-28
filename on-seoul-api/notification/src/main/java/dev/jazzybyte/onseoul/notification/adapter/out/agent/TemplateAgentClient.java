package dev.jazzybyte.onseoul.notification.adapter.out.agent;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplate;
import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.port.out.TemplateGenerationPort;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.instrumentation.annotations.WithSpan;
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

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

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
    @WithSpan("ai.notification.template")
    public TemplateResult generate(NotificationTemplateRequest request) {
        Span.current().setAttribute("notification.service_count", request.services().size());
        try {
            TemplateAgentRequest aiRequest = mapper.toRequest(request);
            // [임시 개발 진단] AI 서비스로 보내는 요청 본문 전체를 DEBUG로 기록한다. 안정화 후 제거할 것.
            if (log.isDebugEnabled()) {
                try {
                    log.debug("[TemplateAgent] AI 요청 payload(serviceCount={}): {}",
                            request.services().size(), OBJECT_MAPPER.writeValueAsString(aiRequest));
                } catch (Exception e) {
                    log.debug("[TemplateAgent] AI 요청 payload 직렬화 실패: {}", e.getMessage());
                }
            }
            TemplateAgentResponse response = webClient.post()
                    .uri("/notification/template")
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(aiRequest)
                    .retrieve()
                    .bodyToMono(TemplateAgentResponse.class)
                    .timeout(Duration.ofSeconds(properties.templateTimeoutSeconds()))
                    .block();

            if (response != null && response.isValid()) {
                log.debug("[TemplateAgent] AI 템플릿 생성 성공: serviceCount={}", request.services().size());
                return mapper.toDomain(response);
            }
            log.warn("[TemplateAgent] AI 응답 유효하지 않음(빈 title/summary), fallback 사용: serviceCount={}", request.services().size());
        } catch (WebClientResponseException e) {
            log.warn("[TemplateAgent] AI 호출 non-2xx({}), fallback 사용: serviceCount={}", e.getStatusCode(), request.services().size());
        } catch (Exception e) {
            if (e.getCause() instanceof InterruptedException) {
                Thread.currentThread().interrupt();
            }
            log.warn("[TemplateAgent] AI 호출 실패({}), fallback 사용: serviceCount={}", e.getClass().getSimpleName(), request.services().size());
        }
        return NotificationTemplate.render(request);
    }
}
