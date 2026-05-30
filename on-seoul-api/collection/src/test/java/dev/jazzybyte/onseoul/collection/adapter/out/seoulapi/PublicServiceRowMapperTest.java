package dev.jazzybyte.onseoul.collection.adapter.out.seoulapi;

import dev.jazzybyte.onseoul.collection.domain.PublicServiceReservation;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;

class PublicServiceRowMapperTest {

    private PublicServiceRowMapper mapper;

    @BeforeEach
    void setUp() {
        mapper = new PublicServiceRowMapper();
    }

    private PublicServiceRow buildValidRow() {
        PublicServiceRow row = new PublicServiceRow();
        setField(row, "svcid", "SVC001");
        setField(row, "svcnm", "테스트 서비스");
        setField(row, "gubun", "문화");
        setField(row, "maxclassnm", "문화행사");
        setField(row, "minclassnm", "공연");
        setField(row, "svcstatnm", "접수중");
        setField(row, "payatnm", "무료");
        setField(row, "placenm", "서울시립미술관");
        setField(row, "areanm", "중구");
        setField(row, "usetgtinfo", "누구나");
        setField(row, "svcurl", "https://yeyak.seoul.go.kr/SVC001");
        setField(row, "imgurl", "https://img.seoul.go.kr/SVC001.jpg");
        setField(row, "dtlcont", "상세 내용");
        setField(row, "telno", "02-1234-5678");
        setField(row, "x", "126.9779");
        setField(row, "y", "37.5665");
        setField(row, "svcopnbgndt", "2025-01-01 09:00:00");
        setField(row, "svcopnenddt", "2025-12-31 18:00:00");
        setField(row, "rcptbgndt", "2025-01-01 09:00:00");
        setField(row, "rcptenddt", "2025-06-30 18:00:00");
        setField(row, "vMin", "09:00");
        setField(row, "vMax", "18:00");
        setField(row, "revstddaynm", "당일취소가능");
        setField(row, "revstdday", "1");
        return row;
    }

    private void setField(PublicServiceRow row, String fieldName, String value) {
        try {
            var field = PublicServiceRow.class.getDeclaredField(fieldName);
            field.setAccessible(true);
            field.set(row, value);
        } catch (NoSuchFieldException | IllegalAccessException e) {
            throw new RuntimeException("필드 설정 실패: " + fieldName, e);
        }
    }

    @Test
    @DisplayName("정상 row → Optional.of() 반환, 각 필드가 올바르게 매핑된다")
    void toEntity_validRow_returnsPresent() {
        PublicServiceRow row = buildValidRow();

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isPresent();
        PublicServiceReservation entity = result.get();
        assertThat(entity.getServiceId()).isEqualTo("SVC001");
        assertThat(entity.getServiceName()).isEqualTo("테스트 서비스");
        assertThat(entity.getServiceGubun()).isEqualTo("문화");
        assertThat(entity.getMaxClassName()).isEqualTo("문화행사");
        assertThat(entity.getMinClassName()).isEqualTo("공연");
        assertThat(entity.getServiceStatus()).isEqualTo("접수중");
        assertThat(entity.getPaymentType()).isEqualTo("무료");
        assertThat(entity.getPlaceName()).isEqualTo("서울시립미술관");
        assertThat(entity.getAreaName()).isEqualTo("중구");
        assertThat(entity.getTargetInfo()).isEqualTo("누구나");
        assertThat(entity.getServiceUrl()).isEqualTo("https://yeyak.seoul.go.kr/SVC001");
        assertThat(entity.getImageUrl()).isEqualTo("https://img.seoul.go.kr/SVC001.jpg");
        assertThat(entity.getDetailContent()).isEqualTo("상세 내용");
        assertThat(entity.getTelNo()).isEqualTo("02-1234-5678");
        assertThat(entity.getCoordX()).isEqualByComparingTo(new BigDecimal("126.9779"));
        assertThat(entity.getCoordY()).isEqualByComparingTo(new BigDecimal("37.5665"));
        assertThat(entity.getCancelStdType()).isEqualTo("당일취소가능");
        assertThat(entity.getCancelStdDays()).isEqualTo((short) 1);
    }

    @Test
    @DisplayName("SVCID가 null이면 Optional.empty()를 반환한다")
    void toEntity_nullServiceId_returnsEmpty() {
        PublicServiceRow row = buildValidRow();
        setField(row, "svcid", null);

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("SVCNM이 null이면 Optional.empty()를 반환한다")
    void toEntity_nullServiceName_returnsEmpty() {
        PublicServiceRow row = buildValidRow();
        setField(row, "svcnm", null);

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("SVCID가 빈 문자열이면 Optional.empty()를 반환한다")
    void toEntity_blankServiceId_returnsEmpty() {
        PublicServiceRow row = buildValidRow();
        setField(row, "svcid", "  ");

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isEmpty();
    }

    @Test
    @DisplayName("날짜 문자열이 'yyyy-MM-dd HH:mm:ss' 포맷이면 LocalDateTime으로 변환된다")
    void toEntity_validDateString_parsesToLocalDateTime() {
        PublicServiceRow row = buildValidRow();
        setField(row, "rcptbgndt", "2025-03-15 10:30:00");

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isPresent();
        assertThat(result.get().getReceiptStartDt())
                .isEqualTo(LocalDateTime.of(2025, 3, 15, 10, 30, 0));
    }

    @Test
    @DisplayName("날짜 문자열이 잘못된 포맷이면 null로 처리하고 예외를 전파하지 않는다")
    void toEntity_invalidDateString_setsNullWithoutException() {
        PublicServiceRow row = buildValidRow();
        setField(row, "rcptbgndt", "not-a-date");

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isPresent();
        assertThat(result.get().getReceiptStartDt()).isNull();
    }

    @Test
    @DisplayName("X/Y 좌표가 null이면 coordX/coordY는 null이다")
    void toEntity_nullCoords_setsNullCoords() {
        PublicServiceRow row = buildValidRow();
        setField(row, "x", null);
        setField(row, "y", null);

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isPresent();
        assertThat(result.get().getCoordX()).isNull();
        assertThat(result.get().getCoordY()).isNull();
    }

    @Test
    @DisplayName("X/Y 좌표가 빈 문자열이면 coordX/coordY는 null이다")
    void toEntity_blankCoords_setsNullCoords() {
        PublicServiceRow row = buildValidRow();
        setField(row, "x", "   ");
        setField(row, "y", "");

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isPresent();
        assertThat(result.get().getCoordX()).isNull();
        assertThat(result.get().getCoordY()).isNull();
    }

    @Test
    @DisplayName("USETGTINFO가 빈 문자열이면 targetInfo는 null로 trim 처리된다")
    void toEntity_blankUsetgtinfo_setsNullTargetInfo() {
        PublicServiceRow row = buildValidRow();
        setField(row, "usetgtinfo", "  ");

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isPresent();
        assertThat(result.get().getTargetInfo()).isNull();
    }
}
