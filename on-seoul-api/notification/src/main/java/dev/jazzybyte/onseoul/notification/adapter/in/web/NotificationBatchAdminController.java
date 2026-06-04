package dev.jazzybyte.onseoul.notification.adapter.in.web;

import dev.jazzybyte.onseoul.notification.application.NotificationScheduler;
import dev.jazzybyte.onseoul.notification.application.ScheduledTriggerScheduler;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 *  임시/개발용 — 운영 배포 전 인증 적용 또는 제거 필요.
 *
 * <p>알림 배치를 스케줄(이벤트/cron)을 기다리지 않고 즉시 1회 실행하기 위한 <b>임시 관리 API</b>다.
 * 로컬/개발 환경에서만 사용한다. 경로 {@code /internal/notifications/batch/**}는
 * {@code SecurityConfig}에서 임시로 {@code permitAll()}로 열려 있다.
 *
 * <p>TODO(SECURITY): 운영 배포 전 반드시 다음 중 하나를 적용한다.
 * <ul>
 *   <li>이 컨트롤러 + {@code permitAll()} 라인 제거, 또는</li>
 *   <li>관리자 인증/인가(ROLE_ADMIN 등) 또는 내부망/IP 화이트리스트로 보호</li>
 * </ul>
 *
 * <p>컨트롤러는 얇게 유지한다 — 비즈니스 로직 없이 스케줄러의 수동 실행 메서드만 호출한다.
 * 각 스케줄러는 기존 {@code AtomicBoolean running} 가드를 공유하므로, 이미 실행 중이면
 * 중복 실행 없이 409(이미 실행 중)를 반환한다. 응답에는 실행한 배치 종류와 상태만 담고
 * 사용자 연락처/식별자 등 PII는 노출하지 않는다.
 */
@Slf4j
@RestController
@RequestMapping("/internal/notifications/batch")
@RequiredArgsConstructor
public class NotificationBatchAdminController {

    private final NotificationScheduler notificationScheduler;
    private final ScheduledTriggerScheduler scheduledTriggerScheduler;

    /** CHANGE 배치(이벤트 기반)를 수동으로 1회 실행한다. */
    @PostMapping("/change")
    public ResponseEntity<BatchRunResponse> runChange() {
        log.warn("[NotificationBatchAdmin] 임시 API — CHANGE 배치 수동 실행 요청");
        NotificationScheduler.ManualRunResult result = notificationScheduler.runManually();
        return toResponse("CHANGE", result == NotificationScheduler.ManualRunResult.RAN);
    }

    /** 시점 트리거 배치(cron 기반)를 수동으로 1회 실행한다. */
    @PostMapping("/scheduled")
    public ResponseEntity<BatchRunResponse> runScheduled() {
        log.warn("[NotificationBatchAdmin] 임시 API — 시점 트리거 배치 수동 실행 요청");
        ScheduledTriggerScheduler.ManualRunResult result = scheduledTriggerScheduler.runManually();
        return toResponse("SCHEDULED", result == ScheduledTriggerScheduler.ManualRunResult.RAN);
    }

    /**
     * 두 배치를 CHANGE → SCHEDULED 순서로 실행한다.
     * 이 순서는 cross-trigger dedup(CHANGE 우선)을 유지하기 위함이다.
     * # CHANGE → 시점 트리거 순서로 두 배치 모두 수동 실행
     * curl -i -X POST httpㄴ://api.jazzz.dev/api/internal/notifications/batch/all
     */
    @PostMapping("/all")
    public ResponseEntity<BatchAllRunResponse> runAll() {
        log.warn("[NotificationBatchAdmin] 임시 API — CHANGE→SCHEDULED 배치 수동 실행 요청");
        boolean changeRan = notificationScheduler.runManually() == NotificationScheduler.ManualRunResult.RAN;
        boolean scheduledRan = scheduledTriggerScheduler.runManually() == ScheduledTriggerScheduler.ManualRunResult.RAN;
        return ResponseEntity.ok(new BatchAllRunResponse(
                new BatchRunResponse("CHANGE", changeRan ? "RAN" : "SKIPPED_ALREADY_RUNNING"),
                new BatchRunResponse("SCHEDULED", scheduledRan ? "RAN" : "SKIPPED_ALREADY_RUNNING")));
    }

    private ResponseEntity<BatchRunResponse> toResponse(String batch, boolean ran) {
        if (ran) {
            return ResponseEntity.ok(new BatchRunResponse(batch, "RAN"));
        }
        // 이미 실행 중 — 가드 우회 없이 409로 알린다.
        return ResponseEntity.status(HttpStatus.CONFLICT)
                .body(new BatchRunResponse(batch, "SKIPPED_ALREADY_RUNNING"));
    }

    /** @param batch 실행한 배치 종류, @param status RAN | SKIPPED_ALREADY_RUNNING */
    public record BatchRunResponse(String batch, String status) {}

    public record BatchAllRunResponse(BatchRunResponse change, BatchRunResponse scheduled) {}
}
