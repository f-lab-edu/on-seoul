package dev.jazzybyte.onseoul.domain.port.in;

import reactor.core.publisher.Flux;

public interface QueryAndStreamUseCase {

    /**
     * AI 서비스에 질의하고 응답 토큰을 스트리밍한다.
     * 스트림이 완료되면 대화 이력(USER + ASSISTANT)을 저장한다.
     *
     * @param command 사용자 ID, 채팅방 ID(nullable), 질문
     * @return 응답 토큰 스트림
     */
    Flux<String> streamAndSave(SendQueryCommand command);
}
