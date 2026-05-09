package dev.jazzybyte.onseoul.repository;

import dev.jazzybyte.onseoul.domain.ChatRoom;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface ChatRoomRepository extends JpaRepository<ChatRoom, Long> {

    List<ChatRoom> findByUserIdOrderByCreatedAtDesc(Long userId);
}
