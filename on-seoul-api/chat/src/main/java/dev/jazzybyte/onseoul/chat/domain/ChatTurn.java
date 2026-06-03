package dev.jazzybyte.onseoul.chat.domain;

/**
 * AI 서비스로 전달할 대화 한 턴(메시지 하나)의 맥락 표현.
 * {@code role}은 LLM 메시지 컨벤션을 따라 소문자("user"/"assistant")이며,
 * {@code content}는 윈도우/길이 캡이 이미 적용된 텍스트다.
 */
public record ChatTurn(String role, String content) {}
