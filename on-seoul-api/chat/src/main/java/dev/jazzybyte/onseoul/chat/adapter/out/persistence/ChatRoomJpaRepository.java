package dev.jazzybyte.onseoul.chat.adapter.out.persistence;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;

public interface ChatRoomJpaRepository extends JpaRepository<ChatRoomJpaEntity, Long> {

    Optional<ChatRoomJpaEntity> findByIdAndUserIdAndDeletedAtIsNull(Long id, Long userId);

    @Query("""
            SELECT r FROM ChatRoomJpaEntity r
            WHERE r.userId = :userId AND r.deletedAt IS NULL
            ORDER BY r.updatedAt DESC, r.id DESC
            """)
    List<ChatRoomJpaEntity> findFirstPage(@Param("userId") Long userId, Pageable pageable);

    @Query("""
            SELECT r FROM ChatRoomJpaEntity r
            WHERE r.userId = :userId AND r.deletedAt IS NULL
              AND (r.updatedAt < :cursorUpdatedAt
                   OR (r.updatedAt = :cursorUpdatedAt AND r.id < :cursorId))
            ORDER BY r.updatedAt DESC, r.id DESC
            """)
    List<ChatRoomJpaEntity> findNextPage(
            @Param("userId") Long userId,
            @Param("cursorUpdatedAt") OffsetDateTime cursorUpdatedAt,
            @Param("cursorId") Long cursorId,
            Pageable pageable);
}
