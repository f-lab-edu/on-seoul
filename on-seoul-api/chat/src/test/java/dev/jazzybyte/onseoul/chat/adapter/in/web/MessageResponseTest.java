package dev.jazzybyte.onseoul.chat.adapter.in.web;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatMessageRole;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * MessageResponse 의 @JsonRawValue serviceCards 직렬화 동작 검증.
 *
 * <p>저장된 service_cards opaque JSON 문자열이 조회 응답에서 이스케이프된 문자열이 아니라
 * 배열/객체 그대로 노출되어야 한다. null이면 JSON null.
 */
class MessageResponseTest {

    // findAndRegisterModules(): JavaTimeModule(JSR-310) 등록 — OffsetDateTime 직렬화 지원.
    // Spring Boot 기본 ObjectMapper도 동일 모듈을 등록하므로 실제 웹 응답과 동작이 일치한다.
    private final ObjectMapper objectMapper = new ObjectMapper().findAndRegisterModules();

    private ChatMessage assistant(String content, String serviceCards) {
        return new ChatMessage(1L, 10L, 2L, ChatMessageRole.ASSISTANT, content, serviceCards,
                OffsetDateTime.parse("2026-06-04T10:00:00Z"));
    }

    @Test
    @DisplayName("serviceCards 배열은 @JsonRawValue로 escape 없이 배열 그대로 직렬화된다")
    void serviceCards_serializedAsRawArray_notEscapedString() throws Exception {
        String cardsJson = "[{\"service_id\":\"S1\",\"name\":\"강남 음악회 🎵\"},{\"service_id\":\"S2\"}]";
        MessageResponse response = MessageResponse.from(assistant("강남구 안내", cardsJson));

        String out = objectMapper.writeValueAsString(response);
        JsonNode tree = objectMapper.readTree(out);

        // 응답 키는 스트리밍 final 이벤트와 동일하게 snake_case(service_cards)로 노출된다.
        JsonNode cards = tree.get("service_cards");
        // 핵심: 이스케이프된 문자열("[{...}]")이 아니라 실제 배열이어야 한다.
        assertThat(cards.isArray()).isTrue();
        assertThat(cards).hasSize(2);
        assertThat(cards.get(0).get("service_id").asText()).isEqualTo("S1");
        // 한글/이모지가 깨지지 않고 보존된다.
        assertThat(cards.get(0).get("name").asText()).isEqualTo("강남 음악회 🎵");

        // raw 문자열에 escape된 따옴표(\")가 카드 영역에 없어야 한다.
        assertThat(out).contains("\"service_cards\":[{");
        assertThat(out).doesNotContain("\"service_cards\":\"[");
    }

    @Test
    @DisplayName("serviceCards가 null이면 JSON null로 직렬화된다")
    void serviceCards_null_serializedAsJsonNull() throws Exception {
        MessageResponse response = MessageResponse.from(assistant("답변", null));

        String out = objectMapper.writeValueAsString(response);
        JsonNode tree = objectMapper.readTree(out);

        assertThat(tree.has("service_cards")).isTrue();
        assertThat(tree.get("service_cards").isNull()).isTrue();
    }

    @Test
    @DisplayName("USER 메시지(serviceCards null)도 JSON null로 직렬화된다")
    void serviceCards_userMessage_serializedAsJsonNull() throws Exception {
        ChatMessage user = new ChatMessage(2L, 10L, 1L, ChatMessageRole.USER, "질문", null,
                OffsetDateTime.parse("2026-06-04T09:59:00Z"));
        MessageResponse response = MessageResponse.from(user);

        JsonNode tree = objectMapper.readTree(objectMapper.writeValueAsString(response));
        assertThat(tree.get("role").asText()).isEqualTo("USER");
        assertThat(tree.get("service_cards").isNull()).isTrue();
    }

    @Test
    @DisplayName("빈 배열 \"[]\"은 그대로 빈 JSON 배열로 직렬화된다")
    void serviceCards_emptyArray_serializedAsEmptyJsonArray() throws Exception {
        MessageResponse response = MessageResponse.from(assistant("답변", "[]"));

        JsonNode tree = objectMapper.readTree(objectMapper.writeValueAsString(response));
        assertThat(tree.get("service_cards").isArray()).isTrue();
        assertThat(tree.get("service_cards")).isEmpty();
    }
}
