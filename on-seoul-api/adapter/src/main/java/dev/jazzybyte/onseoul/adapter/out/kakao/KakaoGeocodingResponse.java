package dev.jazzybyte.onseoul.adapter.out.kakao;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import lombok.ToString;

import java.util.List;

@Getter
@Setter
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
class KakaoGeocodingResponse {

    private List<Document> documents;

    @Getter
    @Setter
    @NoArgsConstructor
    @JsonIgnoreProperties(ignoreUnknown = true)
    @ToString
    static class Document {
        private String x;
        private String y;
        @JsonProperty("place_name")
        private String placeName;
        @JsonProperty("address_name")
        private String addressName;
    }
}
