package dev.jazzybyte.onseoul.collector.exception;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;

/**
 * 서울시 Open API 호출 관련 예외 기반 클래스.
 *
 * <p>{@link SeoulApiServerException}(5xx 재시도 대상)과
 * 4xx 클라이언트 오류를 구분하기 위해 계층을 분리한다.</p>
 */
public class SeoulApiException extends OnSeoulApiException {

    public SeoulApiException(ErrorCode errorCode) {
        super(errorCode);
    }

    public SeoulApiException(ErrorCode errorCode, String detail) {
        super(errorCode, detail);
    }

    public SeoulApiException(ErrorCode errorCode, String detail, Throwable cause) {
        super(errorCode, detail, cause);
    }
}
