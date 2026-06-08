package dev.jazzybyte.onseoul.chat.domain;

import java.util.List;

/**
 * 멀티턴 참조 해소(carryover) 맥락. 직전 assistant 메시지에서 추출해 AI 요청에 동봉한다.
 *
 * @param prevEntities  직전 assistant service_cards에서 추출한 [{service_id, label}]. 순서 보존, 최대 10건.
 *                      직전 assistant 없음/카드 없음/파싱 실패 시 빈 리스트.
 * @param prevIntent    직전 assistant 메시지의 intent. 없으면 null.
 * @param prevReasoning 현 단계 미사용 — 항상 null.
 */
public record Carryover(List<PrevEntity> prevEntities, String prevIntent, String prevReasoning) {

    public static Carryover empty() {
        return new Carryover(List.of(), null, null);
    }
}
