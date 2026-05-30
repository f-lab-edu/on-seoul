package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.NotificationTemplateRequest;
import dev.jazzybyte.onseoul.notification.domain.TemplateResult;

public interface TemplateGenerationPort {
    TemplateResult generate(NotificationTemplateRequest request);
}
