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
    private OffsetDateTime createdAt;

    public ChatMessage(Long id, Long roomId, Long seq, ChatMessageRole role,
                       String content, OffsetDateTime createdAt) {
        this(id, roomId, seq, role, content, null, null, null, createdAt);
    }

    public ChatMessage(Long id, Long roomId, Long seq, ChatMessageRole role,
                       String content, String serviceCards, String intent, OffsetDateTime createdAt) {
        this(id, roomId, seq, role, content, serviceCards, intent, null, createdAt);
    }

    public ChatMessage(Long id, Long roomId, Long seq, ChatMessageRole role,
                       String content, String serviceCards, String intent, String decision,
                       OffsetDateTime createdAt) {
        this.id = id;
        this.roomId = roomId;
        this.seq = seq;
        this.role = role;
        this.content = content;
        this.serviceCards = serviceCards;
        this.intent = intent;
        this.decision = decision;
        this.createdAt = createdAt;
    }

    /** 카드/intent/decision 없는 메시지(USER 등) 생성. serviceCards/intent/decision은 null. */
    public static ChatMessage create(Long roomId, Long seq, ChatMessageRole role, String content) {
        return create(roomId, seq, role, content, null, null, null);
    }

    /** ASSISTANT 메시지를 service_cards(opaque JSON)·intent와 함께 생성. decision은 null. */
    public static ChatMessage create(Long roomId, Long seq, ChatMessageRole role,
                                     String content, String serviceCards, String intent) {
        return create(roomId, seq, role, content, serviceCards, intent, null);
    }

    /** ASSISTANT 메시지를 service_cards·intent·decision(각각 없으면 null)과 함께 생성. */
    public static ChatMessage create(Long roomId, Long seq, ChatMessageRole role,
                                     String content, String serviceCards, String intent, String decision) {
        ChatMessage msg = new ChatMessage();
        msg.roomId = roomId;
        msg.seq = seq;
        msg.role = role;
        msg.content = content;
        msg.serviceCards = serviceCards;
        msg.intent = intent;
        msg.decision = decision;
        msg.createdAt = OffsetDateTime.now();
        return msg;
    }

    private ChatMessage() {}
}
