package dev.jazzybyte.onseoul.chat.adapter.out.persistence;

import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatMessageRole;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.OffsetDateTime;

@Entity
@Table(name = "chat_messages")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class ChatMessageJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "room_id", nullable = false)
    private Long roomId;

    @Column(nullable = false)
    private Long seq;

    @Column(nullable = false, length = 20)
    private String role;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String content;

    // raw JSON text passthrough. ASSISTANT 메시지의 service_cards 배열, USER는 null.
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "service_cards")
    private String serviceCards;

    // ASSISTANT 메시지의 intent(예: "SQL_SEARCH"). USER는 null. 다음 턴 carryover(prev_intent)용.
    @Column(name = "intent", length = 20)
    private String intent;

    // ASSISTANT 메시지의 triage decision(action/routes/user_rationale/sources) opaque JSON. USER는 null.
    // user_rationale을 다음 턴 carryover(prev_reasoning)로 추출하는 데 사용. raw JSON text passthrough.
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "decision")
    private String decision;

    // ASSISTANT 메시지의 prev_working_set(opaque JSON 봉투). USER는 null. raw JSON text passthrough.
    // Spring은 해석하지 않고 통째로 저장했다가 다음 턴 carryover로 verbatim 회신한다. 구 메시지/첫 턴은 null.
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "working_set")
    private String workingSet;

    @Column(name = "created_at", nullable = false)
    private OffsetDateTime createdAt;

    @PrePersist
    void prePersist() {
        if (createdAt == null) createdAt = OffsetDateTime.now();
    }

    /** 일회성 백필용 — service_cards JSON 스냅샷의 디코딩된 값으로 교체한다. */
    void replaceServiceCards(String decoded) {
        this.serviceCards = decoded;
    }

    public ChatMessage toDomain() {
        return new ChatMessage(id, roomId, seq, ChatMessageRole.valueOf(role), content,
                serviceCards, intent, decision, workingSet, createdAt);
    }

    public static ChatMessageJpaEntity fromDomain(ChatMessage message) {
        ChatMessageJpaEntity entity = new ChatMessageJpaEntity();
        entity.id = message.getId();
        entity.roomId = message.getRoomId();
        entity.seq = message.getSeq();
        entity.role = message.getRole().name();
        entity.content = message.getContent();
        entity.serviceCards = message.getServiceCards();
        entity.intent = message.getIntent();
        entity.decision = message.getDecision();
        entity.workingSet = message.getWorkingSet();
        entity.createdAt = message.getCreatedAt();
        return entity;
    }
}
