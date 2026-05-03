package dev.jazzybyte.onseoul.adapter.out.persistence.chat;

import dev.jazzybyte.onseoul.domain.model.ChatMessage;
import dev.jazzybyte.onseoul.domain.model.ChatMessageRole;
import dev.jazzybyte.onseoul.domain.model.ChatRoom;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;


import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

@DataJpaTest
@TestPropertySource(properties = {
        "spring.datasource.url=jdbc:h2:mem:testdb;MODE=PostgreSQL;DATABASE_TO_LOWER=TRUE;DEFAULT_NULL_ORDERING=HIGH",
        "spring.jpa.hibernate.ddl-auto=none",
        "spring.sql.init.mode=embedded"
})
@Import(ChatPersistenceAdapter.class)
class ChatPersistenceAdapterTest {

    @Autowired
    private ChatPersistenceAdapter adapter;

    // ── saveChatRoom ──────────────────────────────────────────────

    @Test
    @DisplayName("saveChatRoom() — 저장 후 채번된 id가 반환된다")
    void saveChatRoom_returnsIdAfterSave() {
        ChatRoom room = ChatRoom.create(1L, "서울 공공서비스 질문");

        ChatRoom saved = adapter.save(room);

        assertThat(saved.getId()).isNotNull().isPositive();
        assertThat(saved.getUserId()).isEqualTo(1L);
        assertThat(saved.getTitle()).isEqualTo("서울 공공서비스 질문");
        assertThat(saved.isTitleGenerated()).isFalse();
    }

    // ── findById ──────────────────────────────────────────────────

    @Test
    @DisplayName("findById() — 존재하는 방 id → Optional.of()")
    void findById_existingRoom_returnsOptionalOf() {
        ChatRoom saved = adapter.save(ChatRoom.create(2L, "체육시설 예약 안내"));

        Optional<ChatRoom> result = adapter.findById(saved.getId());

        assertThat(result).isPresent();
        assertThat(result.get().getId()).isEqualTo(saved.getId());
        assertThat(result.get().getTitle()).isEqualTo("체육시설 예약 안내");
    }

    @Test
    @DisplayName("findById() — 없는 id → Optional.empty()")
    void findById_nonExistentId_returnsEmpty() {
        Optional<ChatRoom> result = adapter.findById(99999L);

        assertThat(result).isEmpty();
    }

    // ── saveChatMessage ───────────────────────────────────────────

    @Test
    @DisplayName("saveChatMessage() — USER role 메시지 저장 성공")
    void saveChatMessage_userRole_savedSuccessfully() {
        ChatRoom room = adapter.save(ChatRoom.create(3L, "테스트 채팅방"));
        Long seq = adapter.nextSeq();
        ChatMessage userMsg = ChatMessage.create(room.getId(), seq, ChatMessageRole.USER, "문화행사 예약 방법 알려줘");

        ChatMessage saved = adapter.save(userMsg);

        assertThat(saved.getId()).isNotNull().isPositive();
        assertThat(saved.getRoomId()).isEqualTo(room.getId());
        assertThat(saved.getRole()).isEqualTo(ChatMessageRole.USER);
        assertThat(saved.getContent()).isEqualTo("문화행사 예약 방법 알려줘");
        assertThat(saved.getSeq()).isEqualTo(seq);
    }

    @Test
    @DisplayName("saveChatMessage() — ASSISTANT role 메시지 저장 성공")
    void saveChatMessage_assistantRole_savedSuccessfully() {
        ChatRoom room = adapter.save(ChatRoom.create(4L, "테스트 채팅방2"));
        Long seq = adapter.nextSeq();
        ChatMessage assistantMsg = ChatMessage.create(room.getId(), seq, ChatMessageRole.ASSISTANT, "문화행사 예약은 서울시 공공서비스 예약 포털에서 가능합니다.");

        ChatMessage saved = adapter.save(assistantMsg);

        assertThat(saved.getId()).isNotNull().isPositive();
        assertThat(saved.getRole()).isEqualTo(ChatMessageRole.ASSISTANT);
        assertThat(saved.getContent()).isEqualTo("문화행사 예약은 서울시 공공서비스 예약 포털에서 가능합니다.");
    }
}
