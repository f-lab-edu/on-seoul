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
}
