package dev.jazzybyte.onseoul.collector.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;
import lombok.NoArgsConstructor;

/**
 * 서울시 공공서비스 예약 Open API 응답의 row 항목 (24개 필드).
 * 필드명은 API 원문 그대로 사용한다 (대문자 + 약어).
 * 날짜/시간/좌표는 모두 String으로 수신하며 변환은 {@link PublicServiceRowMapper}에서 처리한다.
 *
 * <p>날짜 포맷: {@code "yyyy-MM-dd HH:mm:ss.S"} (예: {@code "2026-01-01 00:00:00.0"})</p>
 * <p>이용 시간: V_MIN(시작), V_MAX(종료) — 포맷 {@code "HH:mm"} (예: {@code "09:00"})</p>
 */
@Getter
@NoArgsConstructor
public class PublicServiceRow {

    @JsonProperty("GUBUN")
    private String gubun;               // 서비스 구분 (체육, 문화, 진료 등)

    @JsonProperty("SVCID")
    private String svcid;               // 서비스 ID (고유키)

    @JsonProperty("MAXCLASSNM")
    private String maxclassnm;          // 대분류명

    @JsonProperty("MINCLASSNM")
    private String minclassnm;          // 소분류명

    @JsonProperty("SVCSTATNM")
    private String svcstatnm;           // 서비스 상태명 (접수중, 접수종료, 마감 등)

    @JsonProperty("SVCNM")
    private String svcnm;               // 서비스명

    @JsonProperty("PAYATNM")
    private String payatnm;             // 결제 방법 (무료, 유료 등)

    @JsonProperty("PLACENM")
    private String placenm;             // 장소명

    @JsonProperty("USETGTINFO")
    private String usetgtinfo;          // 이용 대상 정보

    @JsonProperty("SVCURL")
    private String svcurl;              // 서비스 URL

    @JsonProperty("X")
    private String x;                   // X 좌표 — 경도 (nullable, Geocoding fallback 대상)

    @JsonProperty("Y")
    private String y;                   // Y 좌표 — 위도 (nullable, Geocoding fallback 대상)

    @JsonProperty("SVCOPNBGNDT")
    private String svcopnbgndt;         // 서비스 개시 시작일시

    @JsonProperty("SVCOPNENDDT")
    private String svcopnenddt;         // 서비스 개시 종료일시

    @JsonProperty("RCPTBGNDT")
    private String rcptbgndt;           // 접수 시작일시

    @JsonProperty("RCPTENDDT")
    private String rcptenddt;           // 접수 종료일시

    @JsonProperty("AREANM")
    private String areanm;              // 자치구명

    @JsonProperty("IMGURL")
    private String imgurl;              // 이미지 URL

    @JsonProperty("DTLCONT")
    private String dtlcont;             // 상세 내용 (HTML 포함 가능, 원문 저장 / 벡터화 시점에 정제)

    @JsonProperty("TELNO")
    private String telno;               // 전화번호

    @JsonProperty("V_MIN")
    private String vMin;                // 이용 시작 시간 (예: "09:00")

    @JsonProperty("V_MAX")
    private String vMax;                // 이용 종료 시간 (예: "18:00")

    @JsonProperty("REVSTDDAYNM")
    private String revstddaynm;         // 취소 기준 유형명 (예: "이용일")

    @JsonProperty("REVSTDDAY")
    private String revstdday;           // 취소 기준일 수 (예: "5")
}
