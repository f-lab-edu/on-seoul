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
    private OffsetDateTime createdAt;

    public ChatMessage(Long id, Long roomId, Long seq, ChatMessageRole role,
                       String content, OffsetDateTime createdAt) {
        this(id, roomId, seq, role, content, null, null, createdAt);
    }

    public ChatMessage(Long id, Long roomId, Long seq, ChatMessageRole role,
                       String content, String serviceCards, String intent, OffsetDateTime createdAt) {
        this.id = id;
        this.roomId = roomId;
        this.seq = seq;
        this.role = role;
        this.content = content;
        this.serviceCards = serviceCards;
        this.intent = intent;
        this.createdAt = createdAt;
    }

    /** 카드/intent 없는 메시지(USER 등) 생성. serviceCards/intent는 null. */
    public static ChatMessage create(Long roomId, Long seq, ChatMessageRole role, String content) {
        return create(roomId, seq, role, content, null, null);
    }

    /** ASSISTANT 메시지를 service_cards(opaque JSON)·intent(없으면 각각 null)와 함께 생성. */
    public static ChatMessage create(Long roomId, Long seq, ChatMessageRole role,
                                     String content, String serviceCards, String intent) {
        ChatMessage msg = new ChatMessage();
        msg.roomId = roomId;
        msg.seq = seq;
        msg.role = role;
        msg.content = content;
        msg.serviceCards = serviceCards;
        msg.intent = intent;
        msg.createdAt = OffsetDateTime.now();
        return msg;
    }

    private ChatMessage() {}
}
