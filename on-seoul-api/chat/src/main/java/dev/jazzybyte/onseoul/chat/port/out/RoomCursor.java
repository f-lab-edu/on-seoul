package dev.jazzybyte.onseoul.chat.port.out;

import java.time.OffsetDateTime;

/**
 * ChatRoom 커서 페이지네이션의 커서 값.
 * application 과 persistence 어댑터가 공통으로 참조하는 포트-레이어 타입.
 */
public record RoomCursor(OffsetDateTime updatedAt, Long id) {}
