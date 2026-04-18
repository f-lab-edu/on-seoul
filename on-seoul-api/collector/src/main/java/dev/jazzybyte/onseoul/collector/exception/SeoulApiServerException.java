package dev.jazzybyte.onseoul.collector.exception;

import dev.jazzybyte.onseoul.exception.ErrorCode;

/**
 * 서울시 Open API 5xx 서버 오류 — 재시도 대상.
 *
 * <p>WebClient 재시도 필터에서 {@code ex instanceof SeoulApiServerException}으로
 * 4xx({@link SeoulApiException})와 구분하여 5xx만 재시도한다.</p>
 */
public class SeoulApiServerException extends SeoulApiException {

    public SeoulApiServerException(String detail) {
        super(ErrorCode.COLLECT_API_SERVER_ERROR, detail);
    }

    public SeoulApiServerException(String detail, Throwable cause) {
        super(ErrorCode.COLLECT_API_SERVER_ERROR, detail, cause);
    }
}
