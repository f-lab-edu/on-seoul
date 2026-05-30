package dev.jazzybyte.onseoul.notification.adapter.in.web;

import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.port.in.UpdateSubscriptionUseCase.UpdateSubscriptionCommand;

import java.util.Set;

/**
 * filter/channels 모두 nullable — 부분 수정 요청.
 *
 * <p>비즈니스 검증(둘 다 null / 빈 channels 등)은 application service 가 책임진다.
 * 이 DTO 는 단순 매핑만 담당한다.
 */
public record UpdateSubscriptionRequest(
        FilterDto filter,
        Set<NotificationChannel> channels
) {
    public UpdateSubscriptionCommand toCommand() {
        return new UpdateSubscriptionCommand(
                filter != null ? filter.toDomain() : null,
                channels);
    }
}
