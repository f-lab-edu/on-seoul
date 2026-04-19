package dev.jazzybyte.onseoul.collector.dto;

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
public class KakaoGeocodingResponse {

    private List<Document> documents;

    @Getter
    @Setter
    @NoArgsConstructor
    @JsonIgnoreProperties(ignoreUnknown = true)
    @ToString
    public static class Document {
        /** 경도 (longitude) */
        private String x;
        /** 위도 (latitude) */
        private String y;
        @JsonProperty("place_name")
        private String placeName;
        @JsonProperty("address_name")
        private String addressName;
    }
}
