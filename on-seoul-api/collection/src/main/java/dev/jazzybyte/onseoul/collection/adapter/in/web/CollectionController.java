package dev.jazzybyte.onseoul.collection.adapter.in.web;

import dev.jazzybyte.onseoul.collection.adapter.out.persistence.reservation.PublicServiceTextBackfill;
import dev.jazzybyte.onseoul.collection.port.in.CollectDatasetUseCase;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Slf4j
@RestController
@RequestMapping("/admin/collection")
@RequiredArgsConstructor
public class CollectionController {

    private final CollectDatasetUseCase collectDatasetUseCase;
    private final PublicServiceTextBackfill textBackfill;

    @PostMapping("/trigger")
    public ResponseEntity<Void> trigger() {
        log.info("수동 수집 트리거 요청");
        collectDatasetUseCase.collectAll();
        return ResponseEntity.ok().build();
    }

    /**
     * 일회성 관리 API — 기존 행의 HTML-escape 표시 텍스트를 디코딩한다. 멱등이라 재실행 안전.
     * TODO(SECURITY/CLEANUP): 1회 적용 후 제거 권장. 인증된 사용자만 호출 가능(기본 인가 정책).
     */
    @PostMapping("/backfill/html-unescape")
    public ResponseEntity<PublicServiceTextBackfill.BackfillResult> backfillHtmlUnescape() {
        log.warn("[CollectionAdmin] 임시 API — public_service_reservations HTML 디코딩 백필 요청");
        return ResponseEntity.ok(textBackfill.run());
    }
}
