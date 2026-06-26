package dev.jazzybyte.onseoul.chat.domain;

/**
 * 멀티턴 carryover 맥락. 직전 ASSISTANT 메시지에서 추출해 AI 요청에 동봉한다.
 *
 * <p>워킹셋 carryover 전면 전환: AI가 SSE final 에 emit 한 {@code prev_working_set} 객체를 Spring 이
 * opaque JSON 통째로 직전 ASSISTANT 행에 저장하고, 다음 턴에 verbatim 회신한다. Spring 은 이 봉투를
 * 해석하지 않는다(entities/intent/reasoning/refined_query/applied_filters/relaxed/relaxed_filters 등
 * 내부 필드는 AI 계약의 일부이며 API 는 무관). 기존 평면 carryover(prev_entities/prev_intent/
 * prev_reasoning)는 이 nested 봉투로 흡수되었다.
 *
 * @param workingSet 직전 ASSISTANT 의 working_set(opaque JSON 문자열). 직전 ASSISTANT 없음/구 메시지/
 *                   첫 턴이면 null. null 이면 AI 요청에 {@code prev_working_set} 을 싣지 않는다(AI 현행
 *                   동작으로 폴백 — 하위호환).
 */
public record Carryover(String workingSet) {

    public static Carryover empty() {
        return new Carryover(null);
    }
}
