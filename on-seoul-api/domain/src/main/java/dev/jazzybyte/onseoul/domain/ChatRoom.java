package dev.jazzybyte.onseoul.domain;

import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.UpdateTimestamp;

import java.time.OffsetDateTime;

@Entity
@Table(name = "chat_rooms")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class ChatRoom {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Column(name = "title", length = 200)
    private String title;

    @Column(name = "is_title_generated", nullable = false)
    private boolean titleGenerated;

    @Column(name = "created_at", nullable = false, updatable = false,
            columnDefinition = "TIMESTAMPTZ DEFAULT NOW()")
    private OffsetDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "updated_at", nullable = false, columnDefinition = "TIMESTAMPTZ DEFAULT NOW()")
    private OffsetDateTime updatedAt;

    @Builder
    public ChatRoom(User user, String title) {
        this.user = user;
        this.title = title;
        this.titleGenerated = false;
        this.createdAt = OffsetDateTime.now();
    }

    public void updateTitle(String title) {
        this.title = title;
        this.titleGenerated = true;
        // updatedAt is managed by @UpdateTimestamp — no manual assignment needed
    }
}
