package dev.jazzybyte.onseoul.chat.adapter.out.agent;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.chat.domain.PrevEntity;
import dev.jazzybyte.onseoul.chat.port.out.ServiceCardParserPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.List;

/**
 * service_cards(opaque JSON)를 carryover용 {@link PrevEntity}로 파싱한다.
 *
 * <p>JSON 해석은 AI 계약의 일부이므로 ChatAgentClient와 동일하게 adapter/out/agent에 둔다(도메인 opaque 유지).
 * carryover에는 식별자({@code service_id})와 라벨({@code service_name})만 싣고, 사실 필드는 의도적으로 무시한다.
 */
@Slf4j
@Component
public class ServiceCardParser implements ServiceCardParserPort {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    @Override
    public List<PrevEntity> parsePrevEntities(String serviceCardsJson, int limit) {
        if (serviceCardsJson == null || serviceCardsJson.isBlank()) {
            return List.of();
        }
        try {
            JsonNode root = OBJECT_MAPPER.readTree(serviceCardsJson);
            if (!root.isArray()) {
                return List.of();
            }
            List<PrevEntity> entities = new ArrayList<>();
            for (JsonNode card : root) {
                if (entities.size() >= limit) {
                    break;
                }
                JsonNode idNode = card.get("service_id");
                String serviceId = (idNode == null || idNode.isNull()) ? "" : idNode.asText();
                if (serviceId.isBlank()) {
                    continue; // 식별자 없는 카드는 서수 바인딩 대상이 아니므로 제외
                }
                JsonNode nameNode = card.get("service_name");
                String label = (nameNode == null || nameNode.isNull()) ? "" : nameNode.asText();
                entities.add(new PrevEntity(serviceId, label));
            }
            return List.copyOf(entities);
        } catch (Exception e) {
            log.debug("[Chat] service_cards carryover 파싱 실패 - 빈 prev_entities로 폴백");
            return List.of();
        }
    }
}
