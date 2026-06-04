package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Semaphore;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 채팅 생성(AI 스트림 소비)의 per-user / 전역 동시성 cap을 강제한다.
 *
 * <p>클라이언트가 끊겨도 백그라운드 저장 구독이 AI 스트림을 끝까지 소비하므로, cap이 없으면
 * 동시 LLM 호출이 무제한으로 늘어날 수 있다(비용/DoS). 이 가드는 실제 동시 호출 수를 제한한다.</p>
 *
 * <p>획득은 (1) per-user 카운트, (2) 전역 Semaphore 순으로 시도한다. 전역에서 실패하면 이미 올린
 * per-user 카운트를 즉시 롤백해 누수를 막는다. {@link Permit#close()}는 idempotent하며,
 * 정상/에러/취소/타임아웃 등 모든 종료 경로에서 정확히 한 번 해제되도록 보장한다.</p>
 */
@Slf4j
@Component
public class ChatConcurrencyGuard {

    private final int perUserCap;
    private final Semaphore globalPermits;
    private final Duration backgroundTimeout;
    private final ConcurrentHashMap<Long, AtomicInteger> perUserCounts = new ConcurrentHashMap<>();

    public ChatConcurrencyGuard(ChatConcurrencyProperties properties) {
        this.perUserCap = properties.perUser();
        this.globalPermits = new Semaphore(properties.global());
        this.backgroundTimeout = Duration.ofSeconds(properties.backgroundTimeoutSeconds());
    }

    /** 저장 구독(백그라운드 소비)의 상한 타임아웃. 클라 끊김과 무관하게 적용한다. */
    public Duration backgroundTimeout() {
        return backgroundTimeout;
    }

    /** 테스트 전용: 현재 추적 중인 per-user 엔트리 수(0 도달 시 제거되는지 검증용). */
    int trackedUserEntryCount() {
        return perUserCounts.size();
    }

    /**
     * 사용자별/전역 cap을 확인하고 permit을 획득한다. 초과 시
     * {@link OnSeoulApiException}({@link ErrorCode#CHAT_CONCURRENCY_LIMIT}, 429)을 던진다.
     */
    public Permit acquire(long userId) {
        // map-level 원자 연산(compute)으로 증가 + 생성. close()의 제거(compute)와 동일한
        // 원자성 하에서 동작해 "0이면 제거"와 증가가 레이스 없이 일관되게 맞춰진다.
        AtomicInteger userCount = perUserCounts.compute(userId, (k, v) -> {
            AtomicInteger c = (v == null) ? new AtomicInteger() : v;
            c.incrementAndGet();
            return c;
        });

        if (userCount.get() > perUserCap) {
            rollbackPerUser(userId); // map 원자 연산으로 감소 + 0이면 제거
            log.warn("[Chat] per-user 동시 생성 cap 초과 - userId={}, cap={}", userId, perUserCap);
            throw new OnSeoulApiException(ErrorCode.CHAT_CONCURRENCY_LIMIT);
        }

        if (!globalPermits.tryAcquire()) {
            rollbackPerUser(userId); // 전역 거부 시 per-user 롤백(누수 방지)
            log.warn("[Chat] 전역 동시 생성 cap 초과 - userId={}, available={}", userId, globalPermits.availablePermits());
            throw new OnSeoulApiException(ErrorCode.CHAT_CONCURRENCY_LIMIT);
        }

        log.debug("[Chat] 생성 permit 획득 - userId={}, globalAvailable={}",
                userId, globalPermits.availablePermits());
        return new Permit(userId);
    }

    /**
     * per-user 카운트를 1 감소시키고, 0에 도달하면 맵에서 원자적으로 제거한다.
     * compute 람다 내에서 감소·제거를 한 번에 결정하므로 동시 acquire(증가)와 레이스가 없다.
     */
    private void rollbackPerUser(long userId) {
        perUserCounts.compute(userId, (k, v) -> {
            if (v == null) {
                return null; // 방어적: 이미 없으면 그대로 둠
            }
            return v.decrementAndGet() == 0 ? null : v;
        });
    }

    /** 획득한 동시성 권한. {@link #close()}로 정확히 한 번 해제한다(AutoCloseable). */
    public final class Permit implements AutoCloseable {

        private final long userId;
        private final AtomicBoolean released = new AtomicBoolean(false);

        private Permit(long userId) {
            this.userId = userId;
        }

        @Override
        public void close() {
            if (released.compareAndSet(false, true)) {
                rollbackPerUser(userId); // 감소 + 0이면 맵에서 제거(엔트리 누수 방지)
                globalPermits.release();
                log.debug("[Chat] 생성 permit 해제 - userId={}, globalAvailable={}",
                        userId, globalPermits.availablePermits());
            }
        }
    }
}
