package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.port.in.UpdateRoomTitleUseCase;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatRoomPort;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;

@Slf4j
@Service
@RequiredArgsConstructor
public class UpdateRoomTitleService implements UpdateRoomTitleUseCase {

    // chat_rooms.title VARCHAR(200)과 일치. 초과 시 DataIntegrityViolation으로 제목이 조용히 누락되는 것을 방지.
    private static final int TITLE_MAX_LENGTH = 200;

    private final LoadChatRoomPort loadChatRoomPort;
    private final SaveChatRoomPort saveChatRoomPort;

    @Override
    @Transactional
    public void updateRoomTitle(long roomId, String title) {
        // 방어: 빈 제목은 50자 폴백을 덮어쓰지 않는다.
        if (title == null || title.isBlank()) {
            return;
        }

        Optional<ChatRoom> found = loadChatRoomPort.findById(roomId);
        if (found.isEmpty()) {
            // 방이 사라진 경우(삭제 등) — 에러가 아니라 무시(fail-open).
            log.warn("[Chat] 제목 갱신 대상 방 없음 - roomId={}", roomId);
            return;
        }

        ChatRoom room = found.get();
        // soft-delete 가드: 스트림 도중 방이 삭제되면 삭제된 행에 제목을 쓰지 않는다(fail-open, "방 없음"과 동일 사상).
        // findById는 soft-delete를 필터하지 않으므로 여기서 방어한다.
        if (room.isDeleted()) {
            log.debug("[Chat] 제목 갱신 대상 방이 soft-delete됨 - 제목 갱신 스킵 roomId={}", roomId);
            return;
        }
        // 멱등 가드: 이미 AI 생성 제목이 있으면 중복 title 이벤트/재시도에도 덮어쓰지 않는다.
        if (room.isTitleGenerated()) {
            log.debug("[Chat] 이미 AI 생성 제목이 설정됨 - 제목 갱신 스킵 roomId={}", roomId);
            return;
        }

        // DB 길이 캡 방어: VARCHAR(200) 초과 시 DataIntegrityViolation으로 제목이 조용히 누락되는 것을 막는다.
        String cappedTitle = truncate(title, TITLE_MAX_LENGTH);
        room.updateTitle(cappedTitle);
        saveChatRoomPort.save(room);
        // PII 보호: 제목 평문은 INFO 이상으로 로깅하지 않는다(roomId·길이만).
        log.debug("[Chat] AI 생성 제목 갱신 완료 - roomId={}, titleLength={}", roomId, cappedTitle.length());
    }

    /**
     * 코드포인트(글자) 기준으로 절단한다. UTF-16 char 단위 substring과 달리
     * 이모지 등 surrogate pair 경계에서 깨진 문자가 나가지 않도록 보장한다(SendQueryService.truncate와 동형).
     */
    private String truncate(String text, int maxLength) {
        if (text.codePointCount(0, text.length()) <= maxLength) {
            return text;
        }
        int endIndex = text.offsetByCodePoints(0, maxLength);
        return text.substring(0, endIndex);
    }
}
