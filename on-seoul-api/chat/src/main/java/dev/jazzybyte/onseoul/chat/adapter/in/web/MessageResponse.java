package dev.jazzybyte.onseoul.chat.adapter.in.web;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.annotation.JsonRawValue;
import dev.jazzybyte.onseoul.chat.domain.ChatMessage;

import java.time.OffsetDateTime;

public record MessageResponse(
        Long seq,
        String role,
        String content,
        // 저장된 service_cards JSON을 이스케이프 없이 배열 그대로 노출. null이면 null.
        // 키는 스트리밍(SSE) final 이벤트와 동일하게 snake_case(service_cards)로 통일 — 프론트가 두 경로를 동일하게 렌더.
        @JsonProperty("service_cards") @JsonRawValue String serviceCards,
        OffsetDateTime createdAt
) {
    public static MessageResponse from(ChatMessage message) {
        return new MessageResponse(
                message.getSeq(),
                message.getRole().name(),
                message.getContent(),
                message.getServiceCards(),
                message.getCreatedAt()
        );
    }
}
