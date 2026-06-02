package dev.jazzybyte.onseoul.chat.adapter.out.persistence;

import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatMessageRole;
import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.port.out.RoomCursor;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.orm.jpa.DataJpaTest;
import org.springframework.context.annotation.Import;
import org.springframework.test.context.TestPropertySource;

import java.util.List;
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

    // ── findActiveByIdAndUserId ───────────────────────────────────

    @Test
    @DisplayName("findActiveByIdAndUserId() — 활성 방, userId 일치 → Optional.of()")
    void findActiveByIdAndUserId_activeRoom_matchingUser_returnsOptionalOf() {
        ChatRoom saved = adapter.save(ChatRoom.create(10L, "활성 채팅방"));

        Optional<ChatRoom> result = adapter.findActiveByIdAndUserId(saved.getId(), 10L);

        assertThat(result).isPresent();
        assertThat(result.get().getId()).isEqualTo(saved.getId());
    }

    @Test
    @DisplayName("findActiveByIdAndUserId() — soft-delete된 방 → Optional.empty()")
    void findActiveByIdAndUserId_deletedRoom_returnsEmpty() {
        ChatRoom saved = adapter.save(ChatRoom.create(11L, "삭제될 채팅방"));
        saved.softDelete();
        adapter.softDelete(saved);

        Optional<ChatRoom> result = adapter.findActiveByIdAndUserId(saved.getId(), 11L);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("findActiveByIdAndUserId() — 타인 userId → Optional.empty()")
    void findActiveByIdAndUserId_differentUser_returnsEmpty() {
        ChatRoom saved = adapter.save(ChatRoom.create(12L, "다른 사용자 채팅방"));

        Optional<ChatRoom> result = adapter.findActiveByIdAndUserId(saved.getId(), 99L);

        assertThat(result).isEmpty();
    }

    // ── findActiveByUserId ────────────────────────────────────────

    @Test
    @DisplayName("findActiveByUserId() — 첫 페이지, 활성 방만 updated_at DESC, id DESC 반환")
    void findActiveByUserId_firstPage_returnsActiveRoomsInOrder() {
        ChatRoom room1 = adapter.save(ChatRoom.create(20L, "채팅방 A"));
        ChatRoom room2 = adapter.save(ChatRoom.create(20L, "채팅방 B"));
        ChatRoom room3 = adapter.save(ChatRoom.create(20L, "채팅방 C"));

        List<ChatRoom> result = adapter.findActiveByUserId(20L, null, 10);

        assertThat(result).hasSize(3);
        // 나중에 생성될수록 updated_at이 크거나 같고 id가 크므로 역순으로 나와야 한다
        List<Long> ids = result.stream().map(ChatRoom::getId).toList();
        assertThat(ids).containsExactly(room3.getId(), room2.getId(), room1.getId());
    }

    @Test
    @DisplayName("findActiveByUserId() — 키셋 cursor 이후 페이지 정확히 이어짐")
    void findActiveByUserId_withCursor_returnsNextPage() {
        ChatRoom room1 = adapter.save(ChatRoom.create(21L, "페이지 채팅방 1"));
        ChatRoom room2 = adapter.save(ChatRoom.create(21L, "페이지 채팅방 2"));
        ChatRoom room3 = adapter.save(ChatRoom.create(21L, "페이지 채팅방 3"));

        // 첫 페이지: 최신 2개
        List<ChatRoom> firstPage = adapter.findActiveByUserId(21L, null, 2);
        assertThat(firstPage).hasSize(2);
        assertThat(firstPage.get(0).getId()).isEqualTo(room3.getId());
        assertThat(firstPage.get(1).getId()).isEqualTo(room2.getId());

        // 두 번째 페이지: cursor = 마지막 행 기준
        ChatRoom last = firstPage.get(firstPage.size() - 1);
        RoomCursor cursor = new RoomCursor(last.getUpdatedAt(), last.getId());
        List<ChatRoom> secondPage = adapter.findActiveByUserId(21L, cursor, 2);

        assertThat(secondPage).hasSize(1);
        assertThat(secondPage.get(0).getId()).isEqualTo(room1.getId());
    }

    @Test
    @DisplayName("findActiveByUserId() — soft-delete된 방 제외")
    void findActiveByUserId_excludesDeletedRooms() {
        ChatRoom active = adapter.save(ChatRoom.create(22L, "활성 방"));
        ChatRoom toDelete = adapter.save(ChatRoom.create(22L, "삭제될 방"));
        toDelete.softDelete();
        adapter.softDelete(toDelete);

        List<ChatRoom> result = adapter.findActiveByUserId(22L, null, 10);

        assertThat(result).hasSize(1);
        assertThat(result.get(0).getId()).isEqualTo(active.getId());
    }

    // ── softDelete ────────────────────────────────────────────────

    @Test
    @DisplayName("softDelete() — deleted_at 설정 후 findActiveByIdAndUserId에서 empty")
    void softDelete_setsDeletedAt_thenNotFoundAsActive() {
        ChatRoom saved = adapter.save(ChatRoom.create(30L, "소프트딜리트 테스트"));

        saved.softDelete();
        adapter.softDelete(saved);

        Optional<ChatRoom> result = adapter.findActiveByIdAndUserId(saved.getId(), 30L);
        assertThat(result).isEmpty();

        // findById로는 여전히 조회 가능 (soft-delete이므로)
        Optional<ChatRoom> byId = adapter.findById(saved.getId());
        assertThat(byId).isPresent();
        assertThat(byId.get().isDeleted()).isTrue();
    }

    // ── findByRoomIdOrderBySeqAsc ─────────────────────────────────

    @Test
    @DisplayName("findByRoomIdOrderBySeqAsc() — seq ASC 순서로 반환")
    void findByRoomIdOrderBySeqAsc_returnsMessagesInSeqOrder() {
        ChatRoom room = adapter.save(ChatRoom.create(40L, "메시지 순서 테스트"));

        Long seq1 = adapter.nextSeq();
        Long seq2 = adapter.nextSeq();
        Long seq3 = adapter.nextSeq();

        adapter.save(ChatMessage.create(room.getId(), seq3, ChatMessageRole.ASSISTANT, "세 번째"));
        adapter.save(ChatMessage.create(room.getId(), seq1, ChatMessageRole.USER, "첫 번째"));
        adapter.save(ChatMessage.create(room.getId(), seq2, ChatMessageRole.USER, "두 번째"));

        List<ChatMessage> result = adapter.findByRoomIdOrderBySeqAsc(room.getId());

        assertThat(result).hasSize(3);
        assertThat(result.get(0).getSeq()).isEqualTo(seq1);
        assertThat(result.get(1).getSeq()).isEqualTo(seq2);
        assertThat(result.get(2).getSeq()).isEqualTo(seq3);
    }
}
