package dev.jazzybyte.onseoul.exception;

import lombok.Getter;

/**
 * 같은 이메일이 다른 OAuth 벤더로 이미 가입돼 있을 때 발생한다(정책 B — 기존 벤더 안내).
 *
 * <p>기존 가입 provider를 {@link #existingProvider}에 <b>구조적 필드</b>로 담아 전달한다.
 * 핸들러가 예외 <em>메시지 문자열</em>을 스캔해 벤더를 역추출하지 않도록 하기 위함이다
 * (메시지 변경/i18n 시 깨지는 문제 회피).</p>
 *
 * <p>경쟁 조건 백스톱 경로(동시 최초 로그인 → DB 유니크 위반)에서는 기존 벤더를 알 수 없으므로
 * {@code existingProvider}가 {@code null}일 수 있다. 이때 핸들러는 {@code provider} 쿼리를 생략한다.</p>
 */
@Getter
public class EmailConflictException extends OnSeoulApiException {

    /** 기존 가입 벤더. 알 수 없으면(경쟁 조건 백스톱) null. */
    private final String existingProvider;

    public EmailConflictException(String existingProvider, String detail) {
        super(ErrorCode.EMAIL_ALREADY_REGISTERED, detail);
        this.existingProvider = existingProvider;
    }

    public EmailConflictException(String existingProvider, String detail, Throwable cause) {
        super(ErrorCode.EMAIL_ALREADY_REGISTERED, detail, cause);
        this.existingProvider = existingProvider;
    }
}
