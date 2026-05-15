package dev.jazzybyte.onseoul.notification.adapter.out.agent;

import com.fasterxml.jackson.annotation.JsonProperty;

record TemplateAgentRequest(
        @JsonProperty("service_id") String serviceId,
        @JsonProperty("change_type") String changeType,
        @JsonProperty("field_name") String fieldName,
        @JsonProperty("old_value") String oldValue,
        @JsonProperty("new_value") String newValue
) {}
