package dev.jazzybyte.onseoul.chat.port.out;

import dev.jazzybyte.onseoul.chat.domain.Carryover;
import dev.jazzybyte.onseoul.chat.domain.ChatTurn;
import reactor.core.publisher.Flux;

import java.util.List;

public interface AiServiceStreamPort {

    /**
     * AI 서비스 /chat/stream을 호출하고 SSE 이벤트를 {@link AiStreamEvent}로 반환한다.
     *
     * <p>각 이벤트는 프론트로 relay할 원본 data와, final 이벤트인 경우 추출된 answer를 담는다.
     * AI 이벤트 JSON의 파싱은 이 어댑터 구현의 책임이다.
     *
     * @param history     직전 N턴(과거 → 최신). 맥락이 없으면 빈 리스트.
     * @param carryover   멀티턴 참조 해소 맥락(prev_entities/prev_intent/prev_reasoning). 없으면 {@link Carryover#empty()}.
     * @param titleNeeded 새 대화방을 생성하는 첫 턴이면 true(AI가 방 제목을 생성). 기존 방 후속이면 false.
     *                    AI 요청 본문의 {@code title_needed}(boolean)로 항상 명시 전송된다.
     */
    Flux<AiStreamEvent> stream(String question, long roomId, long messageId, Double lat, Double lng,
                               List<ChatTurn> history, Carryover carryover, boolean titleNeeded);
}
