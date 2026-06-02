package dev.jazzybyte.onseoul.chat.adapter.out.agent;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.List;

@JsonInclude(JsonInclude.Include.NON_NULL)
record AiChatRequest(
        @JsonProperty("room_id") long roomId,
        @JsonProperty("message_id") long messageId,
        @JsonProperty("message") String message,
        @JsonProperty("lat") Double lat,
        @JsonProperty("lng") Double lng,
        @JsonProperty("history") List<Turn> history
) {
    record Turn(
            @JsonProperty("role") String role,
            @JsonProperty("content") String content
    ) {}
}
