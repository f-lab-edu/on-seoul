package dev.jazzybyte.onseoul.repository;

import dev.jazzybyte.onseoul.domain.ChatMessage;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

/**
 * WARNING: 호출자는 반드시 chatRoom.getUserId() == authenticatedUserId 를 확인한 뒤 호출해야 한다.
 * room 소유자 검증 없이 roomId만으로 조회하면 IDOR(횡적 권한 탈취) 가 발생할 수 있다.
 */
public interface ChatMessageRepository extends JpaRepository<ChatMessage, Long> {

    List<ChatMessage> findByRoomIdOrderBySeqAsc(Long roomId);
}
