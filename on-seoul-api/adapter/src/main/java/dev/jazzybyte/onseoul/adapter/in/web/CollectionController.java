package dev.jazzybyte.onseoul.adapter.in.web;

import dev.jazzybyte.onseoul.domain.port.in.CollectDatasetUseCase;
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

    @PostMapping("/trigger")
    public ResponseEntity<Void> trigger() {
        log.info("수동 수집 트리거 요청");
        collectDatasetUseCase.collectAll();
        return ResponseEntity.ok().build();
    }
}
