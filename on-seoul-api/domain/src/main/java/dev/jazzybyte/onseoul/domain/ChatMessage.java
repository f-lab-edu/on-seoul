package dev.jazzybyte.onseoul.domain;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.time.OffsetDateTime;

@Entity
@Table(name = "chat_messages")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class ChatMessage {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "room_id", nullable = false)
    private ChatRoom room;

    @Column(name = "seq", nullable = false)
    private Long seq;

    @Enumerated(EnumType.STRING)
    @Column(name = "role", nullable = false, length = 10)
    private ChatMessageRole role;

    @Column(name = "content", nullable = false, columnDefinition = "TEXT")
    private String content;

    @Column(name = "created_at", nullable = false, updatable = false,
            columnDefinition = "TIMESTAMPTZ DEFAULT NOW()")
    private OffsetDateTime createdAt;

    @Builder
    public ChatMessage(ChatRoom room, Long seq, ChatMessageRole role, String content) {
        this.room = room;
        this.seq = seq;
        this.role = role;
        this.content = content;
        this.createdAt = OffsetDateTime.now();
    }
}
