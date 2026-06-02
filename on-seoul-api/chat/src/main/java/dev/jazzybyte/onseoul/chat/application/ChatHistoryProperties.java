package dev.jazzybyte.onseoul.chat.application;

import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * 대화 맥락(history) 윈도우/캡 설정.
 * <ul>
 *   <li>{@code maxTurns} — AI 서비스로 전달할 직전 턴(메시지 쌍) 수. 최대 메시지 수는 maxTurns * 2.</li>
 *   <li>{@code maxCharsPerMessage} — 메시지당 content 길이 캡(초과 시 truncate).</li>
 * </ul>
 */
@ConfigurationProperties(prefix = "chat.history")
public record ChatHistoryProperties(
        int maxTurns,
        int maxCharsPerMessage
) {
    public ChatHistoryProperties {
        if (maxTurns <= 0) maxTurns = 5;
        if (maxCharsPerMessage <= 0) maxCharsPerMessage = 1000;
    }

    public int maxMessages() {
        return maxTurns * 2;
    }
}
