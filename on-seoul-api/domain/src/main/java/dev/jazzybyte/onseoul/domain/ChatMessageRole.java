package dev.jazzybyte.onseoul.domain;

/**
 * 채팅 메시지 역할.
 *
 * <p>DB schema CHECK 제약과 동일한 값을 유지한다: USER, ASSISTANT</p>
 */
public enum ChatMessageRole {
    USER,
    ASSISTANT
}
