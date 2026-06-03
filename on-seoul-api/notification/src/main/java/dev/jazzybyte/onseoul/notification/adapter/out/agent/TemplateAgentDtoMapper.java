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
        // trigger_type 은 enum 이름 그대로 전달(CHANGE/OPEN_DAY/BEFORE_RECEIPT_D1/DEADLINE_DDAY).
        // 시점 트리거는 changes 가 빈 배열일 수 있다(변경 없음).
        return new TemplateAgentRequest(domain.triggerType().name(), groups);
    }

    TemplateResult toDomain(TemplateAgentResponse response) {
        return new TemplateResult(response.title(), response.summary(), TemplateSource.AI);
    }
}
