package dev.jazzybyte.onseoul.chat.adapter.out.persistence;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.databind.node.TextNode;
import dev.jazzybyte.onseoul.util.HtmlTextUtil;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.Iterator;
import java.util.Map;

/**
 * 과거 ASSISTANT 답변에 얼어붙은 service_cards(JSONB 스냅샷)의 HTML-escape 텍스트를 디코딩하는 일회성 백필.
 *
 * <p>JSON 구조/키/순서는 보존하고 <b>문자열 값만</b> 재귀 디코딩한다(url 포함). naive SQL이 못 하던
 * &quot; 포함 카드도 JSON 파싱 → 재직렬화로 유효 JSON을 유지한다. service_cards가 null인 행/USER 메시지는 스킵.
 * 디코딩은 멱등이라 재실행 안전(값 변화 없으면 save 생략).
 */
@Slf4j
@Component
public class ServiceCardsTextBackfill {

    private final ChatMessageJpaRepository repository;
    private final ObjectMapper objectMapper;

    ServiceCardsTextBackfill(final ChatMessageJpaRepository repository, final ObjectMapper objectMapper) {
        this.repository = repository;
        this.objectMapper = objectMapper;
    }

    /** @return [처리(파싱 시도) 건수, 변경 건수] */
    @Transactional
    public BackfillResult run() {
        int processed = 0;
        int changed = 0;
        for (ChatMessageJpaEntity entity : repository.findAll()) {
            String original = entity.getServiceCards();
            if (original == null || original.isBlank()) {
                continue;
            }
            processed++;
            try {
                JsonNode root = objectMapper.readTree(original);
                boolean mutated = decodeValues(root);
                if (!mutated) {
                    continue;
                }
                String decoded = objectMapper.writeValueAsString(root);
                if (!decoded.equals(original)) {
                    entity.replaceServiceCards(decoded);
                    repository.save(entity);
                    changed++;
                }
            } catch (Exception e) {
                log.warn("[ServiceCardsBackfill] JSON 파싱 실패로 스킵 — messageId={}, 사유={}",
                        entity.getId(), e.getMessage());
            }
        }
        log.warn("[ServiceCardsBackfill] chat_messages.service_cards 디코딩 완료 — 처리={}, 변경={}",
                processed, changed);
        return new BackfillResult(processed, changed);
    }

    /** 키는 보존하고 문자열 값(TextNode)만 in-place 디코딩한다. 변경 발생 시 true. */
    private boolean decodeValues(JsonNode node) {
        boolean mutated = false;
        if (node instanceof ObjectNode obj) {
            Iterator<Map.Entry<String, JsonNode>> fields = obj.fields();
            while (fields.hasNext()) {
                Map.Entry<String, JsonNode> field = fields.next();
                JsonNode value = field.getValue();
                if (value instanceof TextNode text) {
                    String decoded = HtmlTextUtil.unescape(text.textValue());
                    if (!decoded.equals(text.textValue())) {
                        obj.set(field.getKey(), TextNode.valueOf(decoded));
                        mutated = true;
                    }
                } else {
                    mutated |= decodeValues(value);
                }
            }
        } else if (node instanceof ArrayNode arr) {
            for (int i = 0; i < arr.size(); i++) {
                JsonNode element = arr.get(i);
                if (element instanceof TextNode text) {
                    String decoded = HtmlTextUtil.unescape(text.textValue());
                    if (!decoded.equals(text.textValue())) {
                        arr.set(i, TextNode.valueOf(decoded));
                        mutated = true;
                    }
                } else {
                    mutated |= decodeValues(element);
                }
            }
        }
        return mutated;
    }

    public record BackfillResult(int processed, int changed) {}
}
