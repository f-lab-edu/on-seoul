package dev.jazzybyte.onseoul.chat.port.out;

/**
 * AI 서비스 SSE 이벤트 1건을 표현한다.
 *
 * <p>AI 이벤트 JSON의 파싱(계약 해석)은 adapter/out/agent의 책임이다. application은
 * {@link #raw}를 프론트로 그대로 relay하고, {@link #finalAnswer}가 존재하면 그 값과
 * {@link #finalServiceCards}, {@link #finalIntent}, {@link #finalWorkingSet}을 저장한다.
 * {@link #decisionJson}이 존재하면 (final보다 먼저 도착하는 별개 decision 이벤트) 그 값을
 * 캡처해 final과 함께 저장한다.
 *
 * @param raw               프론트로 relay할 원본 SSE data(JSON 문자열) — 변경 없이 그대로 전달
 * @param finalAnswer       final 이벤트에서 추출한 답변 텍스트. final이 아니면 null.
 *                          answer 키가 비어 있으면 빈 문자열.
 * @param finalServiceCards final 이벤트의 service_cards 배열을 직렬화한 opaque JSON 문자열.
 *                          카드가 없거나(키 부재/null) final이 아니면 null. API는 내부 구조를
 *                          해석하지 않고 그대로 저장한다.
 * @param finalIntent       final 이벤트의 intent 문자열(예: "SQL_SEARCH"). 키 부재/null/final 아님이면 null.
 *                          다음 턴 carryover(prev_intent)로 영속한다. Spring은 검증 없이 그대로 저장/전달한다.
 * @param decisionJson      decision 이벤트의 payload(action/routes/user_rationale/sources) opaque JSON.
 *                          decision 이벤트가 아니면 null. final/progress와 별개로 도착하며, user_rationale을
 *                          다음 턴 carryover(prev_reasoning)로 추출하는 데 사용한다. 검증 없이 그대로 저장한다.
 * @param finalWorkingSet   final 이벤트의 prev_working_set 객체(opaque JSON 봉투). 키 부재/null/final 아님이면
 *                          null. Spring은 해석하지 않고 통째로 저장했다가 다음 턴 carryover(prev_working_set)로
 *                          verbatim 회신한다.
 * @param title             title 이벤트({@code "type":"title"})에서 추출한 AI 생성 방 제목. title 이벤트가
 *                          아니거나 빈 제목이면 null. 신규 방 첫 턴에만 도착할 수 있으며(미수신 가능, fail-open),
 *                          저장 시 prepared.roomId 기준으로만 영속한다. payload의 room_id/message_id/query는
 *                          캡처하지 않는다(소유자 검증된 roomId만 신뢰).
 */
public record AiStreamEvent(String raw, String finalAnswer, String finalServiceCards, String finalIntent,
                            String decisionJson, String finalWorkingSet, String title) {

    public static AiStreamEvent relay(String raw) {
        return new AiStreamEvent(raw, null, null, null, null, null, null);
    }

    public static AiStreamEvent finalEvent(String raw, String answer) {
        return finalEvent(raw, answer, null, null, null);
    }

    public static AiStreamEvent finalEvent(String raw, String answer, String serviceCardsJson) {
        return finalEvent(raw, answer, serviceCardsJson, null, null);
    }

    public static AiStreamEvent finalEvent(String raw, String answer, String serviceCardsJson, String intent) {
        return finalEvent(raw, answer, serviceCardsJson, intent, null);
    }

    public static AiStreamEvent finalEvent(String raw, String answer, String serviceCardsJson, String intent,
                                           String workingSetJson) {
        return new AiStreamEvent(raw, answer == null ? "" : answer, serviceCardsJson, intent, null, workingSetJson, null);
    }

    /** decision 이벤트. raw는 그대로 relay하고, decisionJson을 캡처해 final과 함께 저장한다. */
    public static AiStreamEvent decisionEvent(String raw, String decisionJson) {
        return new AiStreamEvent(raw, null, null, null, decisionJson, null, null);
    }

    /** title 이벤트. raw는 그대로 relay하고, title 문자열만 캡처해 prepared.roomId 기준으로 영속한다. */
    public static AiStreamEvent titleEvent(String raw, String title) {
        return new AiStreamEvent(raw, null, null, null, null, null, title);
    }

    public boolean isFinal() {
        return finalAnswer != null;
    }

    public boolean isDecision() {
        return decisionJson != null;
    }

    public boolean isTitle() {
        return title != null;
    }
}
