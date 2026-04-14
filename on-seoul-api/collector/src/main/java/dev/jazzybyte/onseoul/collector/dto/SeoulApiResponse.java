package dev.jazzybyte.onseoul.collector.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.util.ArrayList;
import java.util.List;

/**
 * 서울시 Open API 응답의 서비스명 키 하위 객체.
 * 원문 JSON 예시:
 * <pre>
 * {
 *   "ListPublicReservationCulture": {
 *     "list_total_count": 100,
 *     "RESULT": { "CODE": "INFO-000", "MESSAGE": "정상 처리됩니다" },
 *     "row": [ ... ]
 *   }
 * }
 * </pre>
 * 클라이언트에서 서비스명 키를 추출한 뒤 이 클래스로 역직렬화한다.
 */
@Getter
@NoArgsConstructor
public class SeoulApiResponse {

    @JsonProperty("list_total_count")
    private int listTotalCount;

    @JsonProperty("RESULT")
    private Result result;

    @JsonProperty("row")
    private List<PublicServiceRow> rows = new ArrayList<>();

    /** INFO-000: 정상 처리 */
    public boolean isSuccess() {
        return result != null && "INFO-000".equals(result.getCode());
    }

    /** INFO-200: 해당하는 데이터가 없습니다 — 빈 목록이지만 정상 응답 */
    public boolean isNoData() {
        return result != null && "INFO-200".equals(result.getCode());
    }

    @Getter
    @NoArgsConstructor
    public static class Result {

        @JsonProperty("CODE")
        private String code;

        @JsonProperty("MESSAGE")
        private String message;
    }
}
