package dev.jazzybyte.onseoul.controller;

import dev.jazzybyte.onseoul.collector.service.CollectionService;
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

    private final CollectionService collectionService;

    /**
     * 수집 수동 트리거 — 개발/운영 점검용.
     * 수집은 동기로 실행되므로 응답은 수집 완료 후 반환된다.
     */
    @PostMapping("/trigger")
    public ResponseEntity<Void> trigger() {
        log.info("수동 수집 트리거 요청");
        collectionService.collectAll();
        return ResponseEntity.ok().build();
    }
}
