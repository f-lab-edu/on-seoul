package dev.jazzybyte.onseoul.chat.port.in;

import dev.jazzybyte.onseoul.chat.domain.Carryover;
import dev.jazzybyte.onseoul.chat.domain.ChatTurn;

import java.util.List;

public interface SendQueryUseCase {

    /**
     * USER 메시지를 저장하고 roomId와 저장된 메시지의 seq(messageId)를 반환한다.
     * roomId가 null이면 새 ChatRoom을 생성한다.
     */
    PrepareResult prepare(SendQueryCommand command);

    /**
     * 스트림 완료 후 ASSISTANT 응답을 저장한다.
     * seq는 내부에서 nextSeq()를 호출해 채번한다.
     *
     * @param serviceCardsJson AI final 이벤트의 service_cards 배열(opaque JSON 문자열). 카드가
     *                         없으면 null. ASSISTANT 메시지에만 채워지며 그대로 저장된다.
     * @param intent           AI final 이벤트의 intent 문자열(예: "SQL_SEARCH"). 없으면 null.
     *                         다음 턴 carryover(prev_intent)로 사용한다. 검증 없이 그대로 저장된다.
     */
    void saveAnswer(long roomId, String answer, String serviceCardsJson, String intent);

    /**
     * @param created   이번 질의로 새 ChatRoom이 생성되었으면 true, 기존 방이면 false.
     * @param history   현재 질문을 제외한 직전 N턴(과거 → 최신). 맥락이 없으면 빈 리스트.
     * @param carryover 직전 assistant 메시지에서 추출한 멀티턴 참조 해소 맥락. 없으면 {@link Carryover#empty()}.
     */
    record PrepareResult(long roomId, long messageId, boolean created, List<ChatTurn> history, Carryover carryover) {}
}
