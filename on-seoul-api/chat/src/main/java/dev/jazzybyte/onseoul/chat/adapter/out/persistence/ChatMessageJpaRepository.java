package dev.jazzybyte.onseoul.chat.adapter.out.persistence;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

import java.util.List;

public interface ChatMessageJpaRepository extends JpaRepository<ChatMessageJpaEntity, Long> {

    @Query(value = "SELECT nextval('chat_message_seq')", nativeQuery = true)
    Long nextSeq();

    List<ChatMessageJpaEntity> findByRoomIdOrderBySeqAsc(Long roomId);

    /** 직전 N개 메시지를 seq 내림차순(최신 → 과거)으로 윈도우만 조회한다. */
    List<ChatMessageJpaEntity> findByRoomIdOrderBySeqDesc(Long roomId, Pageable pageable);
}
