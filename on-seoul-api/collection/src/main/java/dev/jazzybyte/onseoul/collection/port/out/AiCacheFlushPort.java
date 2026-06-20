package dev.jazzybyte.onseoul.collection.port.out;

/**
 * AI 서비스의 answer cache를 비우는 아웃바운드 포트.
 *
 * <p>데이터 + 임베딩 동기화 완료 후, 캐시된 LLM 답변이 낡지 않도록 강제 무효화한다.
 * best-effort: 호출 실패(타임아웃/5xx/401/네트워크)는 호출자(이벤트 핸들러)가 삼키고
 * WARN 로그만 남긴다. 캐시는 TTL로도 만료되므로 flush 실패가 수집/임베딩 잡을 실패시키지 않는다.
 */
public interface AiCacheFlushPort {

    /**
     * AI 서비스에 answer cache flush를 요청한다.
     *
     * <p>실패 시 예외를 전파한다(호출자가 try/catch로 best-effort 처리).
     */
    void flush();
}
