package dev.jazzybyte.onseoul.chat.port.in;

/**
 * AI가 생성한 방 제목을 영속한다.
 *
 * <p>식별자는 호출자가 소유자 검증을 마친 {@code prepared.roomId} 기준이다(payload room_id 신뢰 금지).
 * 멱등: 이미 AI 생성 제목이 설정된 방({@code titleGenerated == true})은 중복 title 이벤트/재시도에도
 * 덮어쓰지 않는다. title이 null/blank이거나 방이 존재하지 않으면 조용히 무시한다(fail-open).
 */
public interface UpdateRoomTitleUseCase {

    void updateRoomTitle(long roomId, String title);
}
