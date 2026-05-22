package dev.jazzybyte.onseoul.notification.adapter.out.agent;

import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
class TemplateAgentDtoMapper {

    TemplateAgentRequest toRequest(NotificationTemplateRequest domain) {
        List<TemplateAgentRequest.ChangeItem> items = domain.changes().stream()
                .map(c -> new TemplateAgentRequest.ChangeItem(
                        c.changeType(), c.fieldName(), c.oldValue(), c.newValue()))
                .toList();
        return new TemplateAgentRequest(domain.serviceId(), items);
    }

    TemplateResult toDomain(TemplateAgentResponse response) {
        return new TemplateResult(response.title(), response.body(), TemplateSource.AI);
    }
}
