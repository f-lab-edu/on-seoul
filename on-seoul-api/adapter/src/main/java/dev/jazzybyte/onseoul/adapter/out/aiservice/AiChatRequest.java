package dev.jazzybyte.onseoul.adapter.out.aiservice;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonInclude(JsonInclude.Include.NON_NULL)
record AiChatRequest(
        @JsonProperty("room_id") long roomId,
        @JsonProperty("message_id") long messageId,
        @JsonProperty("message") String message,
        @JsonProperty("lat") Double lat,
        @JsonProperty("lng") Double lng
) {}
