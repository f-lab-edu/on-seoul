package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.application.ChatConcurrencyGuard.Permit;
import dev.jazzybyte.onseoul.chat.port.in.QueryAndStreamUseCase;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryCommand;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase;
import dev.jazzybyte.onseoul.chat.port.in.SendQueryUseCase.PrepareResult;
import dev.jazzybyte.onseoul.chat.port.out.AiServiceStreamPort;
import dev.jazzybyte.onseoul.chat.port.out.AiStreamEvent;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Sinks;
import reactor.core.scheduler.Schedulers;

import java.time.Duration;
import java.util.concurrent.atomic.AtomicReference;

/**
 * AI 스트림을 클라이언트 emitter 생명주기와 분리(detach)해 소비/저장한다.
 *
 * <h2>detach 메커니즘</h2>
 * 업스트림 {@link AiStreamEvent} Flux를 <b>정확히 1회</b> 구독(=AI 1회 요청)하고, 그 흐름을 두 소비자가 공유한다.
 * <ul>
 *   <li><b>저장 구독(a)</b>: 끝까지 소비하며 {@code final.answer}를 추출하고, 모든 종료 경로(complete/error/
 *       timeout/cancel)에서 {@code saveAnswer}를 시도한다. <b>클라 끊김과 무관하게 살아 있다.</b></li>
 *   <li><b>relay 흐름(b)</b>: 살아 있는 클라에 raw 토큰을 best-effort로 흘린다. 클라가 끊겨 relay 구독이
 *       취소돼도 (a)는 영향을 받지 않는다.</li>
 * </ul>
 *
 * <h3>공유 방식 선택: {@code Sinks.many().multicast().onBackpressureBuffer()}</h3>
 * {@code .share()}/{@code refCount}는 모든 구독자가 떠나면(클라 disconnect로 relay 취소) 업스트림을 취소하므로
 * 저장이 유실된다 — 부적합. {@code .publish().autoConnect(N)}은 "relay를 한 번도 구독하지 않는(즉시 끊김)"
 * 경우 연결 트리거가 모자라거나, late relay 구독이 이미 흘러간 이벤트를 놓치는 타이밍 문제가 있다.
 * 따라서 <b>저장 구독을 업스트림의 유일한 직접 소비자</b>로 두고, 그 안에서 받은 raw를 sink로 fan-out한다.
 * 저장 구독이 업스트림 수명을 단독으로 소유하므로 relay 취소가 절대 전파되지 않는다(취소 격리).
 *
 * <p>relay sink는 {@code replay().all()}을 쓴다. 저장 구독은 detach되어 즉시 시작하므로, 컨트롤러가
 * relay flux를 구독하기 전에 이벤트가 흘러갈 수 있다. replay 버퍼가 그간의 이벤트를 보관했다가 구독 시
 * 재생하므로, 타이밍과 무관하게 살아 있는 클라가 처음부터 토큰을 받는다(답변 1건 분량이라 메모리 부담 미미).</p>
 */
@Slf4j
@Service
public class ChatStreamService implements QueryAndStreamUseCase {

    private final SendQueryUseCase sendQueryUseCase;
    private final AiServiceStreamPort aiServiceStreamPort;
    private final ChatConcurrencyGuard concurrencyGuard;
    private final Duration backgroundTimeout;

    public ChatStreamService(SendQueryUseCase sendQueryUseCase,
                             AiServiceStreamPort aiServiceStreamPort,
                             ChatConcurrencyGuard concurrencyGuard) {
        this.sendQueryUseCase = sendQueryUseCase;
        this.aiServiceStreamPort = aiServiceStreamPort;
        this.concurrencyGuard = concurrencyGuard;
        this.backgroundTimeout = concurrencyGuard.backgroundTimeout();
    }

    @Override
    public StreamResult streamAndSave(SendQueryCommand command) {
        // 동시성 cap 확인(초과 시 429). prepare/AI 호출 이전에 거부해 비용/DoS를 막는다.
        Permit permit = concurrencyGuard.acquire(command.userId());
        try {
            return launch(command, permit);
        } catch (RuntimeException e) {
            // prepare 등 launch 도중 동기 실패 시 permit 누수 방지(저장 구독이 뜨기 전 경로).
            permit.close();
            throw e;
        }
    }

    private StreamResult launch(SendQueryCommand command, Permit permit) {
        // prepare(@Transactional)는 여기서 동기 실행 — roomId/created를 토큰 구독 전에 확보한다.
        PrepareResult prepared = sendQueryUseCase.prepare(command);

        // final 이벤트의 answer 보관. final 미수신이면 null로 남고, 그 경우 빈 문자열로 저장한다.
        AtomicReference<String> finalAnswer = new AtomicReference<>(null);
        // final 이벤트의 service_cards(opaque JSON) 보관. 카드 미동반이면 null로 남고 그대로 null 저장.
        AtomicReference<String> finalServiceCards = new AtomicReference<>(null);
        // final 이벤트의 intent 보관. 미동반이면 null로 남고 그대로 null 저장(다음 턴 carryover용).
        AtomicReference<String> finalIntent = new AtomicReference<>(null);
        // decision 이벤트의 payload(opaque JSON) 보관. final보다 먼저 도착하는 별개 이벤트라 따로 캡처한다.
        // 미수신이면 null로 남고 그대로 null 저장(하위호환). user_rationale은 다음 턴 carryover(prev_reasoning)용.
        AtomicReference<String> decisionJson = new AtomicReference<>(null);
        // final 이벤트의 prev_working_set 봉투(opaque JSON) 보관. 미동반이면 null로 남고 그대로 null 저장.
        // 다음 턴 carryover(prev_working_set)로 verbatim 회신하기 위해 disconnect 내성 저장 경로로 전달한다.
        AtomicReference<String> finalWorkingSet = new AtomicReference<>(null);

        // relay fan-out 채널. replay().all(): 저장 구독이 즉시 시작해도 클라가 처음부터 토큰을 받도록 버퍼·재생한다.
        Sinks.Many<String> relaySink = Sinks.many().replay().all();

        // (a) 저장 구독 — 업스트림의 유일한 직접 소비자. 클라 끊김과 무관하게 살아 있다.
        aiServiceStreamPort.stream(
                        command.question(), prepared.roomId(), prepared.messageId(),
                        command.lat(), command.lng(), prepared.history(), prepared.carryover())
                .publishOn(Schedulers.boundedElastic())   // 직렬 실행 보장(Netty 이벤트 루프 이탈)
                .timeout(backgroundTimeout)                 // 백그라운드 상한(클라 끊김 무관)
                .doOnNext(event -> {
                    if (event.isFinal()) {
                        finalAnswer.set(event.finalAnswer());
                        finalServiceCards.set(event.finalServiceCards());
                        finalIntent.set(event.finalIntent());
                        finalWorkingSet.set(event.finalWorkingSet());
                    }
                    // decision은 final과 별개로(보통 먼저) 도착한다 — 캡처해뒀다가 doFinally 저장에 함께 전달.
                    if (event.isDecision()) {
                        decisionJson.set(event.decisionJson());
                    }
                    // relay는 best-effort: 구독자가 없거나 버퍼가 차도 저장 흐름은 막지 않는다.
                    relaySink.tryEmitNext(event.raw());
                })
                .doOnError(error -> {
                    log.warn("[Chat] AI 스트림 종료(에러/타임아웃) - roomId={}, msg={}",
                            prepared.roomId(), error.toString());
                    relaySink.tryEmitError(error);
                })
                .doOnComplete(relaySink::tryEmitComplete)
                .doFinally(signal -> {
                    // 모든 종료 경로(complete/error/timeout/cancel)에서 저장 시도 + permit 해제.
                    // 저장 구독은 detach되어 cancel은 사실상 발생하지 않지만, 방어적으로 동일하게 저장한다.
                    try {
                        String answer = finalAnswer.get();
                        sendQueryUseCase.saveAnswer(prepared.roomId(), answer == null ? "" : answer,
                                finalServiceCards.get(), finalIntent.get(), decisionJson.get(), finalWorkingSet.get());
                        log.debug("[Chat] 응답 저장 완료 - roomId={}, signal={}", prepared.roomId(), signal);
                    } catch (Exception e) {
                        log.error("[Chat] ASSISTANT 응답 저장 실패 - roomId={}, signal={}",
                                prepared.roomId(), signal, e);
                    } finally {
                        permit.close();   // 정상/에러/타임아웃/취소 모두에서 정확히 1회 해제
                    }
                })
                .subscribe(
                        event -> { },
                        error -> { /* doOnError/doFinally에서 처리 — 여기서는 삼킨다(저장 구독 격리) */ });

        // (b) relay 흐름 — 클라 emitter가 구독. 취소돼도 sink/저장 구독은 영향 없음.
        return new StreamResult(prepared.roomId(), prepared.created(), relaySink.asFlux());
    }
}
