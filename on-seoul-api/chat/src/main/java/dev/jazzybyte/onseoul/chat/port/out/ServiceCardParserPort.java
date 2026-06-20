package dev.jazzybyte.onseoul.chat.port.out;

import dev.jazzybyte.onseoul.chat.domain.PrevEntity;

import java.util.List;

/**
 * 직전 assistant 메시지의 service_cards(opaque JSON)를 carryover용 {@link PrevEntity} 목록으로 파싱한다.
 *
 * <p>service_cards JSON 해석은 AI 계약의 일부이므로 adapter/out/agent가 구현한다(도메인은 opaque String).
 */
public interface ServiceCardParserPort {

    /**
     * service_cards 배열 JSON에서 {service_id, label=service_name} 쌍을 배열 순서대로 추출한다.
     * service_name이 null이면 label=""로 정규화하되 카드는 유지한다. 최대 {@code limit}건(초과 시 앞쪽).
     * 입력이 null/빈 문자열/배열이 아님/파싱 실패면 빈 리스트를 반환한다.
     */
    List<PrevEntity> parsePrevEntities(String serviceCardsJson, int limit);

    /**
     * decision(opaque JSON)에서 {@code user_rationale}을 추출해 다음 턴 prev_reasoning으로 쓴다.
     * decision JSON 해석도 AI 계약의 일부이므로 adapter가 구현한다(도메인은 opaque String).
     * 입력이 null/빈 문자열/객체 아님/키 부재/null/blank/파싱 실패면 null을 반환한다(하위호환).
     */
    String parseUserRationale(String decisionJson);
}
