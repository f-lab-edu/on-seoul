package dev.jazzybyte.onseoul.domain.port.out;

import reactor.core.publisher.Flux;

public interface AiServiceStreamPort {

    /**
     * AI 서비스 /chat/stream을 호출하고 SSE 이벤트의 data 값을 Flux<String>으로 반환한다.
     */
    Flux<String> stream(String question, Long roomId);
}
