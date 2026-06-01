package dev.jazzybyte.onseoul.notification.adapter.out.agent;

import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;
import dev.jazzybyte.onseoul.notification.domain.TemplateSource;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
class TemplateAgentDtoMapper {

    TemplateAgentRequest toRequest(NotificationTemplateRequest domain) {
        List<TemplateAgentRequest.ServiceChangeGroup> groups = domain.services().stream()
                .map(g -> new TemplateAgentRequest.ServiceChangeGroup(
                        g.serviceId(), g.serviceName(), g.serviceUrl(), g.imageUrl(),
                        g.placeName(), g.areaName(), g.serviceStatus(), g.targetInfo(),
                        g.receiptStartDt(), g.receiptEndDt(),
                        g.changes().stream()
                                .map(c -> new TemplateAgentRequest.ChangeItem(
                                        c.changeType(), c.fieldName(), c.oldValue(), c.newValue()))
                                .toList()))
                .toList();
        return new TemplateAgentRequest(groups);
    }

    TemplateResult toDomain(TemplateAgentResponse response) {
        return new TemplateResult(response.title(), response.body(), TemplateSource.AI);
    }
}
