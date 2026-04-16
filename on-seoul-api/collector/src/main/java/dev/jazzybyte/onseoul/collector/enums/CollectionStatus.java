package dev.jazzybyte.onseoul.collector.enums;

/**
 * 수집 작업의 결과 상태
 */
public enum CollectionStatus {
    /**
     *  수집이 성공적으로 완료된 경우
     */
    SUCCESS,
    /**
     * 수집이 실패한 경우 (예: API 오류, 네트워크 문제 등)
     */
    FAILED,
    /**
     * 수집이 완료되었지만, 일부 데이터가 누락되거나 오류가 발생한 경우
     */
    PARTIAL
}
