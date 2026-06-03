package dev.jazzybyte.onseoul.chat.port.in;

import reactor.core.publisher.Flux;

public interface QueryAndStreamUseCase {

    /**
     * AI 서비스에 질의하고 응답을 스트리밍한다.
     *
     * <p>이 메서드는 호출 시점에 {@code prepare}(@Transactional)를 동기 실행하여 귀속 방 정보를
     * 먼저 확정한다. 따라서 반환된 {@link StreamResult#roomId()}/{@link StreamResult#created()}는
     * 토큰 구독 이전에 즉시 사용할 수 있다(예: SSE init 이벤트 선행 emit).
     *
     * <p>{@code prepare}가 실패하면 토큰 Flux가 아니라 이 메서드 호출에서 예외가 던져진다.
     *
     * @param command 사용자 ID, 채팅방 ID(nullable), 질문, 위치(nullable)
     * @return roomId/created와 응답 토큰 스트림을 담은 결과
     */
    StreamResult streamAndSave(SendQueryCommand command);

    /**
     * @param roomId  이번 응답이 귀속되는 방 ID(신규/기존 모두)
     * @param created 이번 질의로 새로 생성된 방이면 true, 기존 방이면 false
     * @param tokens  프론트로 relay할 SSE data 토큰 스트림(step/final JSON)
     */
    record StreamResult(long roomId, boolean created, Flux<String> tokens) {}
}
