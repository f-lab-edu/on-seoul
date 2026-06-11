package dev.jazzybyte.onseoul.collection.adapter.out.agent;

import dev.jazzybyte.onseoul.collection.port.out.AiCacheFlushPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Component;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.util.retry.Retry;

import java.time.Duration;

/**
 * AI 서비스 {@code POST /admin/cache/flush} 호출 어댑터.
 *
 * <p>데이터·임베딩 동기화 완료 후 LLM answer cache를 무효화한다.
 * 내부 관리 엔드포인트이므로 사용자 JWT가 아닌 {@code X-Internal-Token} 공유 시크릿으로 인증한다.
 *
 * <p>짧은 타임아웃 + 재시도 1회. 실패는 예외로 전파하며, best-effort 처리(WARN 로그)는
 * 호출자(이벤트 핸들러)가 담당한다. EmbeddingSyncClient의 WebClient/.block() 패턴을 따른다.
 */
@Slf4j
@Component
class AiCacheFlushClient implements AiCacheFlushPort {

    private static final String INTERNAL_TOKEN_HEADER = "X-Internal-Token";

    private final WebClient webClient;
    private final EmbeddingSyncProperties properties;

    AiCacheFlushClient(@Qualifier("embeddingSyncWebClient") WebClient webClient,
                       EmbeddingSyncProperties properties) {
        this.webClient = webClient;
        this.properties = properties;
    }

    @Override
    public void flush() {
        Duration timeout = Duration.ofSeconds(properties.adminCacheFlushTimeoutSeconds());

        AiCacheFlushResponse response = webClient.post()
                .uri("/admin/cache/flush")
                .header(INTERNAL_TOKEN_HEADER, properties.adminInternalToken())
                .retrieve()
                .bodyToMono(AiCacheFlushResponse.class)
                .timeout(timeout)
                // 재시도 1회: 일시적 네트워크/타임아웃 흔들림만 만회한다. 시크릿은 로그에 남기지 않는다.
                .retryWhen(Retry.fixedDelay(1, Duration.ofMillis(200)))
                .block();

        int deleted = response != null && response.deleted() != null ? response.deleted() : 0;
        log.info("[AiCacheFlush] AI answer cache flush 완료: deleted={}", deleted);
    }
}
