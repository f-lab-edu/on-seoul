package dev.jazzybyte.onseoul.notification.adapter.out.agent;

import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import org.springframework.stereotype.Component;

@Component
class TemplateAgentDtoMapper {

    TemplateAgentRequest toRequest(NotificationTemplateRequest domain) {
        return new TemplateAgentRequest(
                domain.serviceId(), domain.changeType(),
                domain.fieldName(), domain.oldValue(), domain.newValue());
    }

    TemplateResult toDomain(TemplateAgentResponse response) {
        return new TemplateResult(response.title(), response.body(), TemplateSource.AI);
    }
}
