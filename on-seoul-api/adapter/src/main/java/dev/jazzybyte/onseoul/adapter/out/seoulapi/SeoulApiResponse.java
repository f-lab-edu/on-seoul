package dev.jazzybyte.onseoul.adapter.out.seoulapi;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;
import lombok.NoArgsConstructor;

import java.util.ArrayList;
import java.util.List;

@Getter
@NoArgsConstructor
class SeoulApiResponse {

    @JsonProperty("list_total_count")
    private int listTotalCount;

    @JsonProperty("RESULT")
    private Result result;

    @JsonProperty("row")
    private List<PublicServiceRow> rows = new ArrayList<>();

    boolean isSuccess() {
        return result != null && "INFO-000".equals(result.getCode());
    }

    boolean isNoData() {
        return result != null && "INFO-200".equals(result.getCode());
    }

    @Getter
    @NoArgsConstructor
    static class Result {

        @JsonProperty("CODE")
        private String code;

        @JsonProperty("MESSAGE")
        private String message;
    }
}
