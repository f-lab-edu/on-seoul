package dev.jazzybyte.onseoul.chat.adapter.out.persistence;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatMessageRole;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:testdb;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded"
})
@Import({ServiceCardsTextBackfill.class, ChatPersistenceAdapter.class,
        ServiceCardsTextBackfillTest.ObjectMapperTestConfig.class})
class ServiceCardsTextBackfillTest {

    @TestConfiguration
    static class ObjectMapperTestConfig {
        @Bean
        ObjectMapper objectMapper() {
            return new ObjectMapper();
        }
    }

    private final ObjectMapper objectMapper = new ObjectMapper();

    @Autowired
    private ServiceCardsTextBackfill backfill;

    @Autowired
    private ChatPersistenceAdapter adapter;

    @Autowired
    private ChatMessageJpaRepository repository;

    private ChatMessage saveAssistant(Long roomId, String serviceCardsJson) {
        ChatMessage msg = ChatMessage.create(roomId, adapter.nextSeq(), ChatMessageRole.ASSISTANT,
                "답변 내용", serviceCardsJson, "SQL_SEARCH");
        return adapter.save(msg);
    }

    @Test
    @DisplayName("run() — 카드 객체의 문자열 값(+url)을 재귀 디코딩하고 키/구조는 보존한다")
    void run_decodesStringValuesPreservingKeys() throws Exception {
        String escaped = "[{\"service_name\":\"&lt;(아동)&gt; 프로그램\","
                + "\"place_name\":\"서울&middot;중구\","
                + "\"url\":\"https://x?a=1&amp;b=2\","
                + "\"nested\":{\"desc\":\"It&#39;s 좋음\"}}]";
        ChatMessage saved = saveAssistant(1L, escaped);

        ServiceCardsTextBackfill.BackfillResult result = backfill.run();

        assertThat(result.changed()).isEqualTo(1);
        JsonNode card = objectMapper.readTree(
                repository.findById(saved.getId()).orElseThrow().getServiceCards()).get(0);
        assertThat(card.get("service_name").asText()).isEqualTo("<(아동)> 프로그램");
        assertThat(card.get("place_name").asText()).isEqualTo("서울·중구");
        assertThat(card.get("url").asText()).isEqualTo("https://x?a=1&b=2");
        assertThat(card.get("nested").get("desc").asText()).isEqualTo("It's 좋음");
    }

    @Test
    @DisplayName("run() — &quot; 포함 카드도 유효 JSON으로 재저장된다(naive SQL이 못 하던 케이스)")
    void run_decodesQuoteEntityIntoValidJson() throws Exception {
        String escaped = "[{\"service_name\":\"&quot;인용&quot; 행사\"}]";
        ChatMessage saved = saveAssistant(2L, escaped);

        backfill.run();

        String stored = repository.findById(saved.getId()).orElseThrow().getServiceCards();
        JsonNode root = objectMapper.readTree(stored); // 파싱 성공 = 유효 JSON
        assertThat(root.get(0).get("service_name").asText()).isEqualTo("\"인용\" 행사");
    }

    @Test
    @DisplayName("run() — service_cards가 null인 행/USER 메시지는 스킵한다")
    void run_skipsNullAndUserMessages() {
        adapter.save(ChatMessage.create(3L, adapter.nextSeq(), ChatMessageRole.USER, "질문"));
        saveAssistant(3L, "[{\"service_name\":\"&lt;카드&gt;\"}]");

        ServiceCardsTextBackfill.BackfillResult result = backfill.run();

        assertThat(result.processed()).isEqualTo(1); // ASSISTANT(카드 보유) 1건만 처리
        assertThat(result.changed()).isEqualTo(1);
    }

    @Test
    @DisplayName("run() — 이미 디코딩된 카드는 변경 0건(멱등 재실행 안전)")
    void run_isIdempotent() {
        saveAssistant(4L, "[{\"service_name\":\"&lt;카드&gt;\"}]");
        backfill.run();

        ServiceCardsTextBackfill.BackfillResult second = backfill.run();

        assertThat(second.processed()).isEqualTo(1);
        assertThat(second.changed()).isZero();
    }
}
