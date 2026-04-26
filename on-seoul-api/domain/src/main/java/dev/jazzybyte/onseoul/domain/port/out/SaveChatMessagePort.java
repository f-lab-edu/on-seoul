package dev.jazzybyte.onseoul.domain.port.out;

import dev.jazzybyte.onseoul.domain.model.ChatMessage;

public interface SaveChatMessagePort {
    ChatMessage save(ChatMessage message);

    /** DB Sequence chat_message_seq에서 다음 seq 값을 조회한다. */
    Long nextSeq();
}
