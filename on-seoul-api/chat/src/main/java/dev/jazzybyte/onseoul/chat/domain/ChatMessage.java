package dev.jazzybyte.onseoul.chat.domain;

import lombok.Getter;

import java.time.OffsetDateTime;

@Getter
public class ChatMessage {

    private Long id;
    private Long roomId;
    private Long seq;
    private ChatMessageRole role;
    private String content;
    /** AI final 이벤트의 service_cards 배열(opaque JSON). ASSISTANT만 보유, USER는 null. */
    private String serviceCards;
    /** AI final 이벤트의 intent(예: "SQL_SEARCH"). ASSISTANT만 보유, USER는 null. 다음 턴 carryover(prev_intent)로 사용. */
    private String intent;
    /**
     * AI triage decision(action/routes/user_rationale/sources)의 opaque JSON. ASSISTANT만 보유, USER는 null.
     * triage가 LLM 분류한 턴에만 동반될 수 있어 ASSISTANT여도 null일 수 있다(하위호환). user_rationale을
     * 다음 턴 carryover(prev_reasoning)로 추출하는 데 사용한다(JSON 해석은 adapter 책임 — 도메인은 opaque).
     */
    private String decision;
    /**
     * AI final 이벤트의 prev_working_set(opaque JSON 봉투). ASSISTANT만 보유, USER는 null.
     * Spring은 해석하지 않고 통째로 저장했다가 다음 턴 carryover(prev_working_set)로 verbatim 회신한다.
     * 구 메시지/첫 턴/미동반이면 null(하위호환 — None 수용).
     */
    private String workingSet;
    private OffsetDateTime createdAt;

    public ChatMessage(Long id, Long roomId, Long seq, ChatMessageRole role,
                       String content, OffsetDateTime createdAt) {
        this(id, roomId, seq, role, content, null, null, null, null, createdAt);
    }

    public ChatMessage(Long id, Long roomId, Long seq, ChatMessageRole role,
                       String content, String serviceCards, String intent, OffsetDateTime createdAt) {
        this(id, roomId, seq, role, content, serviceCards, intent, null, null, createdAt);
    }

    public ChatMessage(Long id, Long roomId, Long seq, ChatMessageRole role,
                       String content, String serviceCards, String intent, String decision,
                       OffsetDateTime createdAt) {
        this(id, roomId, seq, role, content, serviceCards, intent, decision, null, createdAt);
    }

    public ChatMessage(Long id, Long roomId, Long seq, ChatMessageRole role,
                       String content, String serviceCards, String intent, String decision,
                       String workingSet, OffsetDateTime createdAt) {
        this.id = id;
        this.roomId = roomId;
        this.seq = seq;
        this.role = role;
        this.content = content;
        this.serviceCards = serviceCards;
        this.intent = intent;
        this.decision = decision;
        this.workingSet = workingSet;
        this.createdAt = createdAt;
    }

    /** 카드/intent/decision/working_set 없는 메시지(USER 등) 생성. 모두 null. */
    public static ChatMessage create(Long roomId, Long seq, ChatMessageRole role, String content) {
        return create(roomId, seq, role, content, null, null, null, null);
    }

    /** ASSISTANT 메시지를 service_cards(opaque JSON)·intent와 함께 생성. decision/working_set은 null. */
    public static ChatMessage create(Long roomId, Long seq, ChatMessageRole role,
                                     String content, String serviceCards, String intent) {
        return create(roomId, seq, role, content, serviceCards, intent, null, null);
    }

    /** ASSISTANT 메시지를 service_cards·intent·decision(각각 없으면 null)과 함께 생성. working_set은 null. */
    public static ChatMessage create(Long roomId, Long seq, ChatMessageRole role,
                                     String content, String serviceCards, String intent, String decision) {
        return create(roomId, seq, role, content, serviceCards, intent, decision, null);
    }

    /** ASSISTANT 메시지를 service_cards·intent·decision·working_set(각각 없으면 null)과 함께 생성. */
    public static ChatMessage create(Long roomId, Long seq, ChatMessageRole role,
                                     String content, String serviceCards, String intent, String decision,
                                     String workingSet) {
        ChatMessage msg = new ChatMessage();
        msg.roomId = roomId;
        msg.seq = seq;
        msg.role = role;
        msg.content = content;
        msg.serviceCards = serviceCards;
        msg.intent = intent;
        msg.decision = decision;
        msg.workingSet = workingSet;
        msg.createdAt = OffsetDateTime.now();
        return msg;
    }

    private ChatMessage() {}
}
