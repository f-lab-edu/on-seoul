package dev.jazzybyte.onseoul.exception;

import lombok.Getter;

/**
 * on-seoul 전역 기반 예외.
 *
 * <p>모든 도메인 예외는 이 클래스를 상속하며, 반드시 {@link ErrorCode}를 지정해야 한다.
 * {@code errorCode}는 HTTP 상태 코드 및 에러 식별자를 포함하므로
 * {@code @ControllerAdvice}에서 일관된 에러 응답을 생성할 수 있다.</p>
 *
 * <pre>{@code
 * // 하위 모듈 예시
 * public class SeoulApiException extends OnSeoulApiException {
 *     public SeoulApiException(ErrorCode code, String detail) {
 *         super(code, detail);
 *     }
 * }
 * }</pre>
 */
@Getter
public class OnSeoulApiException extends RuntimeException {

    private final ErrorCode errorCode;

    public OnSeoulApiException(ErrorCode errorCode) {
        super(errorCode.getDefaultMessage());
        this.errorCode = errorCode;
    }

    public OnSeoulApiException(ErrorCode errorCode, String detail) {
        super(detail);
        this.errorCode = errorCode;
    }

    public OnSeoulApiException(ErrorCode errorCode, String detail, Throwable cause) {
        super(detail, cause);
        this.errorCode = errorCode;
    }
}
