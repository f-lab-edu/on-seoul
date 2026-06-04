package dev.jazzybyte.onseoul.chat.application;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 채팅 동시 생성(LLM 호출) 가드 설정.
 *
 * <p>클라이언트 연결과 무관하게 백그라운드에서 AI 스트림을 끝까지 소비하므로, 동시 LLM
 * 호출 수가 무제한으로 늘어나면 비용/DoS 위험이 있다. 아래 cap으로 실제 동시 호출 수를 제한한다.</p>
 *
 * <ul>
 *   <li>{@code perUser} — 사용자당 동시 진행 가능한 생성 수. 초과 시 429.</li>
 *   <li>{@code global} — 전역 동시 진행 가능한 생성 수(총량 상한).</li>
 *   <li>{@code backgroundTimeoutSeconds} — 저장 구독(백그라운드 소비)의 상한 타임아웃.
 *       클라 끊김과 무관하게 적용된다.</li>
 * </ul>
 */
@ConfigurationProperties(prefix = "chat.concurrency")
public record ChatConcurrencyProperties(
        int perUser,
        int global,
        int backgroundTimeoutSeconds
) {
    public ChatConcurrencyProperties {
        if (perUser <= 0) perUser = 2;
        if (global <= 0) global = 50;
        if (backgroundTimeoutSeconds <= 0) backgroundTimeoutSeconds = 120;
    }
}
