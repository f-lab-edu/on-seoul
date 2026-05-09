package dev.jazzybyte.onseoul.adapter.out.persistence.chat;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface ChatMessageJpaRepository extends JpaRepository<ChatMessageJpaEntity, Long> {

    @Query(value = "SELECT nextval('chat_message_seq')", nativeQuery = true)
    Long nextSeq();
}
