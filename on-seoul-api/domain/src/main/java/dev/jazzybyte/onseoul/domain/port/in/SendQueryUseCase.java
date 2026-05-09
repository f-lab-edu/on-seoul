package dev.jazzybyte.onseoul.domain.port.in;

public interface SendQueryUseCase {

    /**
     * USER 메시지를 저장하고 roomId와 저장된 메시지의 seq(messageId)를 반환한다.
     * roomId가 null이면 새 ChatRoom을 생성한다.
     */
    PrepareResult prepare(SendQueryCommand command);

    /**
     * 스트림 완료 후 ASSISTANT 응답을 저장한다.
     * seq는 내부에서 nextSeq()를 호출해 채번한다.
     */
    void saveAnswer(long roomId, String answer);

    record PrepareResult(long roomId, long messageId) {}
}
