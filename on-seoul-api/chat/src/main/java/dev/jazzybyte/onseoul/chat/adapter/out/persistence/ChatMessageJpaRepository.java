package dev.jazzybyte.onseoul.chat.adapter.out.persistence;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

import java.util.List;

public interface ChatMessageJpaRepository extends JpaRepository<ChatMessageJpaEntity, Long> {

    @Query(value = "SELECT nextval('chat_message_seq')", nativeQuery = true)
    Long nextSeq();

    List<ChatMessageJpaEntity> findByRoomIdOrderBySeqAsc(Long roomId);
}
