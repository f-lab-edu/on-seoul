package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.application.ChatConcurrencyGuard.Permit;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ChatConcurrencyGuardTest {

    private ChatConcurrencyGuard guard(int perUser, int global) {
        return new ChatConcurrencyGuard(new ChatConcurrencyProperties(perUser, global, 120));
    }

    @Test
    @DisplayName("acquire() - per-user cap 초과 시 CHAT_CONCURRENCY_LIMIT(429)를 던진다")
    void acquire_perUserCapExceeded_throws429() {
        ChatConcurrencyGuard guard = guard(2, 100);

        Permit p1 = guard.acquire(1L);
        Permit p2 = guard.acquire(1L);

        assertThatThrownBy(() -> guard.acquire(1L))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.CHAT_CONCURRENCY_LIMIT));

        p1.close();
        p2.close();
    }

    @Test
    @DisplayName("acquire() - 다른 사용자는 per-user cap에 독립적으로 카운트된다")
    void acquire_perUserCap_independentPerUser() {
        ChatConcurrencyGuard guard = guard(1, 100);

        Permit u1 = guard.acquire(1L);
        Permit u2 = guard.acquire(2L); // 다른 사용자는 영향 없음

        assertThat(u1).isNotNull();
        assertThat(u2).isNotNull();
        u1.close();
        u2.close();
    }

    @Test
    @DisplayName("acquire() - 전역 cap 초과 시 CHAT_CONCURRENCY_LIMIT(429)를 던진다")
    void acquire_globalCapExceeded_throws429() {
        ChatConcurrencyGuard guard = guard(100, 2);

        Permit p1 = guard.acquire(1L);
        Permit p2 = guard.acquire(2L);

        assertThatThrownBy(() -> guard.acquire(3L))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.CHAT_CONCURRENCY_LIMIT));

        p1.close();
        p2.close();
    }

    @Test
    @DisplayName("close() - permit 해제 후 다시 acquire할 수 있다(누수 없음)")
    void close_releasesPermit_canReacquire() {
        ChatConcurrencyGuard guard = guard(1, 1);

        Permit p1 = guard.acquire(1L);
        p1.close();

        // 해제되었으므로 다시 획득 가능해야 한다
        Permit p2 = guard.acquire(1L);
        assertThat(p2).isNotNull();
        p2.close();
    }

    @Test
    @DisplayName("close() - 중복 호출해도 한 번만 해제된다(idempotent, 음수 누수 방지)")
    void close_calledTwice_releasesOnce() {
        ChatConcurrencyGuard guard = guard(1, 1);

        Permit p1 = guard.acquire(1L);
        p1.close();
        p1.close(); // 중복 close — 추가 release되면 안 됨

        Permit p2 = guard.acquire(1L);
        // p2를 잡은 상태에서 global(1) 한도가 정확히 1이어야 한다
        assertThatThrownBy(() -> guard.acquire(2L))
                .isInstanceOf(OnSeoulApiException.class);
        p2.close();
    }

    @Test
    @DisplayName("전역 cap 초과로 거부될 때 per-user 카운트는 증가하지 않는다(부분 획득 롤백)")
    void acquire_globalRejected_doesNotLeakPerUserCount() {
        ChatConcurrencyGuard guard = guard(5, 1);

        Permit p1 = guard.acquire(1L); // global 소진

        // user 2가 global 한도로 거부됨 — user 2의 per-user 카운트는 0으로 롤백되어야 한다
        assertThatThrownBy(() -> guard.acquire(2L)).isInstanceOf(OnSeoulApiException.class);

        p1.close();
        // global이 비었으므로 user 2가 정상 획득 가능
        Permit p2 = guard.acquire(2L);
        assertThat(p2).isNotNull();
        p2.close();
    }

    @Test
    @DisplayName("close() - 카운트가 0이 되면 per-user 맵 엔트리가 제거된다(누수 없음)")
    void close_countReachesZero_removesMapEntry() {
        ChatConcurrencyGuard guard = guard(2, 100);

        Permit p1 = guard.acquire(1L);
        Permit p2 = guard.acquire(1L);
        assertThat(guard.trackedUserEntryCount()).isEqualTo(1);

        p1.close();
        // 아직 0이 아니므로 엔트리 유지
        assertThat(guard.trackedUserEntryCount()).isEqualTo(1);

        p2.close();
        // 0 도달 → 엔트리 제거되어야 한다
        assertThat(guard.trackedUserEntryCount()).isZero();
    }

    @Test
    @DisplayName("acquire/release 반복 후 서로 다른 userId 엔트리가 누적되지 않는다")
    void repeatedAcquireRelease_doesNotAccumulateEntries() {
        ChatConcurrencyGuard guard = guard(1, 100);

        for (long userId = 1; userId <= 1_000; userId++) {
            Permit p = guard.acquire(userId);
            p.close();
        }

        assertThat(guard.trackedUserEntryCount()).isZero();
    }

    @Test
    @DisplayName("cap 초과 거부도 엔트리를 누수시키지 않는다(롤백 시 0이면 제거)")
    void rejectedAcquire_doesNotLeakEntry() {
        ChatConcurrencyGuard guard = guard(1, 100);

        Permit p1 = guard.acquire(1L);
        assertThatThrownBy(() -> guard.acquire(1L)).isInstanceOf(OnSeoulApiException.class);

        // 거부된 두 번째 획득의 증가분이 롤백됐고, p1은 아직 살아있으므로 엔트리 1개 유지
        assertThat(guard.trackedUserEntryCount()).isEqualTo(1);
        p1.close();
        assertThat(guard.trackedUserEntryCount()).isZero();
    }

    @Test
    @DisplayName("동시 acquire/release 경합에서 카운트 일관성 + 엔트리 누수 없음(스트레스)")
    void concurrentAcquireRelease_consistentAndNoLeak() throws InterruptedException {
        int threads = 16;
        int iterationsPerThread = 500;
        int users = 4;
        // perUser/global cap을 충분히 크게 두어 거부 없이 순수 경합만 측정한다.
        ChatConcurrencyGuard guard = guard(threads + 1, threads + 1);

        ExecutorService pool = Executors.newFixedThreadPool(threads);
        CountDownLatch start = new CountDownLatch(1);
        CountDownLatch done = new CountDownLatch(threads);
        AtomicInteger failures = new AtomicInteger();

        try {
            for (int t = 0; t < threads; t++) {
                final long userId = (t % users) + 1L;
                pool.submit(() -> {
                    try {
                        start.await();
                        for (int i = 0; i < iterationsPerThread; i++) {
                            try (Permit ignored = guard.acquire(userId)) {
                                // no-op: 즉시 해제
                            }
                        }
                    } catch (Exception e) {
                        failures.incrementAndGet();
                    } finally {
                        done.countDown();
                    }
                });
            }
            start.countDown();
            assertThat(done.await(30, TimeUnit.SECONDS)).isTrue();
        } finally {
            pool.shutdownNow();
        }

        assertThat(failures).hasValue(0);
        // 모든 permit이 해제됐으므로 잔여 엔트리가 없어야 한다(누수 없음).
        assertThat(guard.trackedUserEntryCount()).isZero();

        // 글로벌 한도도 완전히 회복돼 cap만큼 다시 획득 가능해야 한다(카운트 일관성).
        List<Permit> held = new ArrayList<>();
        for (int i = 0; i <= threads; i++) {
            held.add(guard.acquire(99L + i));
        }
        held.forEach(Permit::close);
        assertThat(guard.trackedUserEntryCount()).isZero();
    }
}
