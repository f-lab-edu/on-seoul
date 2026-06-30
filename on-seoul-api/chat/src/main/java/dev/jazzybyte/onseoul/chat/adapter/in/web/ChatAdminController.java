package dev.jazzybyte.onseoul.chat.adapter.in.web;

import dev.jazzybyte.onseoul.chat.adapter.out.persistence.ServiceCardsTextBackfill;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 임시/개발용 관리 API. 운영 배포 전 제거 또는 인증/인가 강화 필요.
 * 기본 인가 정책상 인증된 사용자만 호출 가능하다.
 */
@Slf4j
@RestController
@RequestMapping("/admin/chat")
@RequiredArgsConstructor
public class ChatAdminController {

    private final ServiceCardsTextBackfill serviceCardsTextBackfill;

    /**
     * 일회성 관리 API — chat_messages.service_cards(JSONB)의 HTML-escape 문자열 값을 디코딩한다.
     * 멱등이라 재실행 안전. TODO(CLEANUP): 1회 적용 후 제거 권장.
     */
    @PostMapping("/backfill/service-cards-html-unescape")
    public ResponseEntity<ServiceCardsTextBackfill.BackfillResult> backfillServiceCards() {
        log.warn("[ChatAdmin] 임시 API — chat_messages.service_cards HTML 디코딩 백필 요청");
        return ResponseEntity.ok(serviceCardsTextBackfill.run());
    }
}
