package dev.jazzybyte.onseoul.collector;

import dev.jazzybyte.onseoul.collector.dto.PublicServiceRow;
import dev.jazzybyte.onseoul.domain.PublicServiceReservation;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.util.function.Consumer;

import static org.assertj.core.api.Assertions.assertThat;

class PublicServiceRowMapperTest {

    private PublicServiceRowMapper mapper;

    @BeforeEach
    void setUp() {
        mapper = new PublicServiceRowMapper();
    }

    @Test
    @DisplayName("API 응답 row의 모든 필드가 엔티티로 올바르게 매핑된다")
    void maps_all_fields_correctly() {
        PublicServiceRow row = row(b -> b
                .svcid("S251230171728158726")
                .gubun("자체")
                .maxclassnm("공간시설")
                .minclassnm("강당")
                .svcstatnm("접수종료")
                .svcnm("서울역사박물관 야주개홀(강당) (26. 4월)")
                .payatnm("유료(요금안내문의)")
                .placenm("서울역사박물관")
                .usetgtinfo(" 제한없음")
                .svcurl("https://yeyak.seoul.go.kr/web/reservation/selectReservView.do?rsv_svc_id=S251230171728158726")
                .x("126.97037430869801")
                .y("37.570500279648634")
                .svcopnbgndt("2026-01-01 00:00:00.0")
                .svcopnenddt("2026-04-30 00:00:00.0")
                .rcptbgndt("2026-01-01 00:01:00.0")
                .rcptenddt("2026-02-15 23:59:00.0")
                .areanm("종로구")
                .imgurl("https://yeyak.seoul.go.kr/web/common/file/FileDown.do?file_id=abc")
                .dtlcont("상세내용")
                .telno("02-724-0159")
                .vMin("09:00")
                .vMax("18:00")
                .revstddaynm("이용일")
                .revstdday("5")
        );

        PublicServiceReservation entity = mapper.toEntity(row);

        assertThat(entity.getServiceId()).isEqualTo("S251230171728158726");
        assertThat(entity.getServiceGubun()).isEqualTo("자체");
        assertThat(entity.getMaxClassName()).isEqualTo("공간시설");
        assertThat(entity.getMinClassName()).isEqualTo("강당");
        assertThat(entity.getServiceStatus()).isEqualTo("접수종료");
        assertThat(entity.getServiceName()).isEqualTo("서울역사박물관 야주개홀(강당) (26. 4월)");
        assertThat(entity.getPaymentType()).isEqualTo("유료(요금안내문의)");
        assertThat(entity.getPlaceName()).isEqualTo("서울역사박물관");
        assertThat(entity.getTargetInfo()).isEqualTo("제한없음");   // 앞 공백 trim
        assertThat(entity.getAreaName()).isEqualTo("종로구");
        assertThat(entity.getTelNo()).isEqualTo("02-724-0159");
        assertThat(entity.getCancelStdType()).isEqualTo("이용일");
        assertThat(entity.getCancelStdDays()).isEqualTo((short) 5);
    }

    @Nested
    @DisplayName("날짜 파싱")
    class DateTimeParsing {

        @Test
        @DisplayName("서울시 API 날짜 포맷(yyyy-MM-dd HH:mm:ss.S)을 파싱한다")
        void parses_seoul_api_datetime_format() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .rcptbgndt("2026-01-01 00:01:00.0")
                    .rcptenddt("2026-02-15 23:59:00.0")
            );

            PublicServiceReservation entity = mapper.toEntity(row);

            assertThat(entity.getReceiptStartDt()).isEqualTo(LocalDateTime.of(2026, 1, 1, 0, 1, 0));
            assertThat(entity.getReceiptEndDt()).isEqualTo(LocalDateTime.of(2026, 2, 15, 23, 59, 0));
        }

        @Test
        @DisplayName("소수점 초가 없는 날짜 포맷(yyyy-MM-dd HH:mm:ss)도 파싱한다")
        void parses_datetime_without_fraction() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .rcptbgndt("2026-03-01 09:00:00")
            );

            PublicServiceReservation entity = mapper.toEntity(row);

            assertThat(entity.getReceiptStartDt()).isEqualTo(LocalDateTime.of(2026, 3, 1, 9, 0, 0));
        }

        @Test
        @DisplayName("날짜 값이 null이면 엔티티 필드도 null이다")
        void null_datetime_maps_to_null() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .rcptbgndt(null)
            );

            assertThat(mapper.toEntity(row).getReceiptStartDt()).isNull();
        }

        @Test
        @DisplayName("날짜 값이 빈 문자열이면 엔티티 필드는 null이다")
        void blank_datetime_maps_to_null() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .rcptbgndt("   ")
            );

            assertThat(mapper.toEntity(row).getReceiptStartDt()).isNull();
        }

        @Test
        @DisplayName("잘못된 날짜 포맷은 null로 처리하고 예외를 던지지 않는다")
        void invalid_datetime_maps_to_null_without_exception() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .rcptbgndt("invalid-date")
            );

            assertThat(mapper.toEntity(row).getReceiptStartDt()).isNull();
        }
    }

    @Nested
    @DisplayName("이용 시간 파싱 (V_MIN / V_MAX)")
    class TimeParsing {

        @Test
        @DisplayName("HH:mm 포맷의 이용 시간을 LocalTime으로 파싱한다")
        void parses_use_time_correctly() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .vMin("09:00")
                    .vMax("18:00")
            );

            PublicServiceReservation entity = mapper.toEntity(row);

            assertThat(entity.getUseTimeStart()).isEqualTo(LocalTime.of(9, 0));
            assertThat(entity.getUseTimeEnd()).isEqualTo(LocalTime.of(18, 0));
        }

        @Test
        @DisplayName("이용 시간이 null이면 엔티티 필드도 null이다")
        void null_time_maps_to_null() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .vMin(null).vMax(null)
            );

            assertThat(mapper.toEntity(row).getUseTimeStart()).isNull();
            assertThat(mapper.toEntity(row).getUseTimeEnd()).isNull();
        }
    }

    @Nested
    @DisplayName("좌표 파싱 (X / Y)")
    class CoordinateParsing {

        @Test
        @DisplayName("좌표 문자열을 BigDecimal로 변환한다")
        void parses_coordinates_to_bigdecimal() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .x("126.97037430869801")
                    .y("37.570500279648634")
            );

            PublicServiceReservation entity = mapper.toEntity(row);

            assertThat(entity.getCoordX()).isEqualByComparingTo(new BigDecimal("126.97037430869801"));
            assertThat(entity.getCoordY()).isEqualByComparingTo(new BigDecimal("37.570500279648634"));
        }

        @Test
        @DisplayName("좌표 값이 null이면 엔티티 필드도 null이다 (Geocoding fallback 대상)")
        void null_coordinate_maps_to_null() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .x(null).y(null)
            );

            assertThat(mapper.toEntity(row).getCoordX()).isNull();
            assertThat(mapper.toEntity(row).getCoordY()).isNull();
        }

        @Test
        @DisplayName("좌표 값이 빈 문자열이면 엔티티 필드는 null이다")
        void blank_coordinate_maps_to_null() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .x("").y("")
            );

            assertThat(mapper.toEntity(row).getCoordX()).isNull();
            assertThat(mapper.toEntity(row).getCoordY()).isNull();
        }
    }

    @Nested
    @DisplayName("취소 기준일 파싱 (REVSTDDAY)")
    class CancelDayParsing {

        @Test
        @DisplayName("숫자 문자열을 Short로 변환한다")
        void parses_cancel_std_day_to_short() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .revstdday("5")
            );

            assertThat(mapper.toEntity(row).getCancelStdDays()).isEqualTo((short) 5);
        }

        @Test
        @DisplayName("취소 기준일이 null이면 엔티티 필드도 null이다")
        void null_cancel_day_maps_to_null() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .revstdday(null)
            );

            assertThat(mapper.toEntity(row).getCancelStdDays()).isNull();
        }
    }

    @Nested
    @DisplayName("문자열 정제 (trimToNull)")
    class TrimToNull {

        @Test
        @DisplayName("앞뒤 공백을 제거한다")
        void trims_whitespace() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .usetgtinfo(" 제한없음 ")
            );

            assertThat(mapper.toEntity(row).getTargetInfo()).isEqualTo("제한없음");
        }

        @Test
        @DisplayName("공백만 있는 문자열은 null로 처리한다")
        void blank_string_maps_to_null() {
            PublicServiceRow row = row(b -> b
                    .svcid("SVC-001").svcstatnm("접수중").svcnm("테스트")
                    .telno("   ")
            );

            assertThat(mapper.toEntity(row).getTelNo()).isNull();
        }
    }

    // ────────────────────────────────────────────
    // 헬퍼
    // ────────────────────────────────────────────

    private PublicServiceRow row(Consumer<RowBuilder> config) {
        RowBuilder builder = new RowBuilder();
        config.accept(builder);
        return builder.build();
    }

    /** 테스트용 PublicServiceRow 빌더 (리플렉션으로 private 필드 주입) */
    static class RowBuilder {
        private String svcid, gubun, maxclassnm, minclassnm, svcstatnm, svcnm;
        private String payatnm, placenm, usetgtinfo, svcurl, x, y;
        private String svcopnbgndt, svcopnenddt, rcptbgndt, rcptenddt;
        private String areanm, imgurl, dtlcont, telno;
        private String vMin, vMax, revstddaynm, revstdday;

        RowBuilder svcid(String v)       { this.svcid = v; return this; }
        RowBuilder gubun(String v)       { this.gubun = v; return this; }
        RowBuilder maxclassnm(String v)  { this.maxclassnm = v; return this; }
        RowBuilder minclassnm(String v)  { this.minclassnm = v; return this; }
        RowBuilder svcstatnm(String v)   { this.svcstatnm = v; return this; }
        RowBuilder svcnm(String v)       { this.svcnm = v; return this; }
        RowBuilder payatnm(String v)     { this.payatnm = v; return this; }
        RowBuilder placenm(String v)     { this.placenm = v; return this; }
        RowBuilder usetgtinfo(String v)  { this.usetgtinfo = v; return this; }
        RowBuilder svcurl(String v)      { this.svcurl = v; return this; }
        RowBuilder x(String v)           { this.x = v; return this; }
        RowBuilder y(String v)           { this.y = v; return this; }
        RowBuilder svcopnbgndt(String v) { this.svcopnbgndt = v; return this; }
        RowBuilder svcopnenddt(String v) { this.svcopnenddt = v; return this; }
        RowBuilder rcptbgndt(String v)   { this.rcptbgndt = v; return this; }
        RowBuilder rcptenddt(String v)   { this.rcptenddt = v; return this; }
        RowBuilder areanm(String v)      { this.areanm = v; return this; }
        RowBuilder imgurl(String v)      { this.imgurl = v; return this; }
        RowBuilder dtlcont(String v)     { this.dtlcont = v; return this; }
        RowBuilder telno(String v)       { this.telno = v; return this; }
        RowBuilder vMin(String v)        { this.vMin = v; return this; }
        RowBuilder vMax(String v)        { this.vMax = v; return this; }
        RowBuilder revstddaynm(String v) { this.revstddaynm = v; return this; }
        RowBuilder revstdday(String v)   { this.revstdday = v; return this; }

        PublicServiceRow build() {
            try {
                PublicServiceRow row = new PublicServiceRow();
                set(row, "svcid", svcid);
                set(row, "gubun", gubun);
                set(row, "maxclassnm", maxclassnm);
                set(row, "minclassnm", minclassnm);
                set(row, "svcstatnm", svcstatnm);
                set(row, "svcnm", svcnm);
                set(row, "payatnm", payatnm);
                set(row, "placenm", placenm);
                set(row, "usetgtinfo", usetgtinfo);
                set(row, "svcurl", svcurl);
                set(row, "x", x);
                set(row, "y", y);
                set(row, "svcopnbgndt", svcopnbgndt);
                set(row, "svcopnenddt", svcopnenddt);
                set(row, "rcptbgndt", rcptbgndt);
                set(row, "rcptenddt", rcptenddt);
                set(row, "areanm", areanm);
                set(row, "imgurl", imgurl);
                set(row, "dtlcont", dtlcont);
                set(row, "telno", telno);
                set(row, "vMin", vMin);
                set(row, "vMax", vMax);
                set(row, "revstddaynm", revstddaynm);
                set(row, "revstdday", revstdday);
                return row;
            } catch (Exception e) {
                throw new RuntimeException(e);
            }
        }

        private void set(Object target, String fieldName, Object value) throws Exception {
            var field = target.getClass().getDeclaredField(fieldName);
            field.setAccessible(true);
            field.set(target, value);
        }
    }
}
