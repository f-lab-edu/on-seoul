package dev.jazzybyte.onseoul.adapter.out.seoulapi;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;
import lombok.NoArgsConstructor;

@Getter
@NoArgsConstructor
class PublicServiceRow {

    @JsonProperty("GUBUN")
    private String gubun;

    @JsonProperty("SVCID")
    private String svcid;

    @JsonProperty("MAXCLASSNM")
    private String maxclassnm;

    @JsonProperty("MINCLASSNM")
    private String minclassnm;

    @JsonProperty("SVCSTATNM")
    private String svcstatnm;

    @JsonProperty("SVCNM")
    private String svcnm;

    @JsonProperty("PAYATNM")
    private String payatnm;

    @JsonProperty("PLACENM")
    private String placenm;

    @JsonProperty("USETGTINFO")
    private String usetgtinfo;

    @JsonProperty("SVCURL")
    private String svcurl;

    @JsonProperty("X")
    private String x;

    @JsonProperty("Y")
    private String y;

    @JsonProperty("SVCOPNBGNDT")
    private String svcopnbgndt;

    @JsonProperty("SVCOPNENDDT")
    private String svcopnenddt;

    @JsonProperty("RCPTBGNDT")
    private String rcptbgndt;

    @JsonProperty("RCPTENDDT")
    private String rcptenddt;

    @JsonProperty("AREANM")
    private String areanm;

    @JsonProperty("IMGURL")
    private String imgurl;

    @JsonProperty("DTLCONT")
    private String dtlcont;

    @JsonProperty("TELNO")
    private String telno;

    @JsonProperty("V_MIN")
    private String vMin;

    @JsonProperty("V_MAX")
    private String vMax;

    @JsonProperty("REVSTDDAYNM")
    private String revstddaynm;

    @JsonProperty("REVSTDDAY")
    private String revstdday;
}
