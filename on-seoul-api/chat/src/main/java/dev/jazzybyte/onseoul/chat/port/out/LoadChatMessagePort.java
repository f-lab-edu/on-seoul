package dev.jazzybyte.onseoul.chat.port.out;

import dev.jazzybyte.onseoul.chat.domain.ChatMessage;

import java.util.List;

public interface LoadChatMessagePort {
    List<ChatMessage> findByRoomIdOrderBySeqAsc(Long roomId);

    /**
     * 직전 N개 메시지를 seq 오름차순(과거 → 최신)으로 반환한다.
     * 내부적으로 seq 내림차순 + limit으로 윈도우만 읽은 뒤 오름차순으로 정렬해 돌려준다.
     */
    List<ChatMessage> findRecentByRoomIdOrderBySeqAsc(Long roomId, int limit);
}
