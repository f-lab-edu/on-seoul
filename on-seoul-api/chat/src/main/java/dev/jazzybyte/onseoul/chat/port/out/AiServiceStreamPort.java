package dev.jazzybyte.onseoul.chat.port.out;

import dev.jazzybyte.onseoul.chat.domain.ChatTurn;
import reactor.core.publisher.Flux;

import java.util.List;

public interface AiServiceStreamPort {

    /**
     * AI 서비스 /chat/stream을 호출하고 SSE 이벤트의 data 값을 Flux<String>으로 반환한다.
     *
     * @param history 직전 N턴(과거 → 최신). 맥락이 없으면 빈 리스트.
     */
    Flux<String> stream(String question, long roomId, long messageId, Double lat, Double lng,
                        List<ChatTurn> history);
}
