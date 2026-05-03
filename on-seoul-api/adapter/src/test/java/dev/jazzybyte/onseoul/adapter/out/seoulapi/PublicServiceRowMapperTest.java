package dev.jazzybyte.onseoul.adapter.out.seoulapi;

import dev.jazzybyte.onseoul.domain.model.PublicServiceReservation;
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

    // -----------------------------------------------------------------------
    // 헬퍼: 유효한 기본 row 빌더
    // -----------------------------------------------------------------------
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

    /** 리플렉션으로 package-private 필드에 값을 설정한다. */
    private void setField(PublicServiceRow row, String fieldName, String value) {
        try {
            var field = PublicServiceRow.class.getDeclaredField(fieldName);
            field.setAccessible(true);
            field.set(row, value);
        } catch (NoSuchFieldException | IllegalAccessException e) {
            throw new RuntimeException("필드 설정 실패: " + fieldName, e);
        }
    }

    // -----------------------------------------------------------------------
    // T5-1: 정상 필드 전체 → Optional.of() 반환, 각 필드 매핑 정확성
    // -----------------------------------------------------------------------
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

    // -----------------------------------------------------------------------
    // T5-2: serviceId null → Optional.empty()
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("SVCID가 null이면 Optional.empty()를 반환한다")
    void toEntity_nullServiceId_returnsEmpty() {
        PublicServiceRow row = buildValidRow();
        setField(row, "svcid", null);

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isEmpty();
    }

    // -----------------------------------------------------------------------
    // T5-3: serviceName null → Optional.empty()
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("SVCNM이 null이면 Optional.empty()를 반환한다")
    void toEntity_nullServiceName_returnsEmpty() {
        PublicServiceRow row = buildValidRow();
        setField(row, "svcnm", null);

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isEmpty();
    }

    // -----------------------------------------------------------------------
    // T5-4: serviceId 빈 문자열 → Optional.empty()
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("SVCID가 빈 문자열이면 Optional.empty()를 반환한다")
    void toEntity_blankServiceId_returnsEmpty() {
        PublicServiceRow row = buildValidRow();
        setField(row, "svcid", "  ");

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isEmpty();
    }

    // -----------------------------------------------------------------------
    // T5-5: 날짜 문자열 정상 포맷 → LocalDateTime 변환
    // -----------------------------------------------------------------------
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

    // -----------------------------------------------------------------------
    // T5-6: 날짜 문자열 이상 포맷 → null 처리 (예외 전파 없음)
    // -----------------------------------------------------------------------
    @Test
    @DisplayName("날짜 문자열이 잘못된 포맷이면 null로 처리하고 예외를 전파하지 않는다")
    void toEntity_invalidDateString_setsNullWithoutException() {
        PublicServiceRow row = buildValidRow();
        setField(row, "rcptbgndt", "not-a-date");

        Optional<PublicServiceReservation> result = mapper.toEntity(row);

        assertThat(result).isPresent();
        assertThat(result.get().getReceiptStartDt()).isNull();
    }

    // -----------------------------------------------------------------------
    // T5-7: X/Y 좌표 null → coordX/coordY null
    // -----------------------------------------------------------------------
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

    // -----------------------------------------------------------------------
    // T5-8: X/Y 좌표 빈 문자열 → null 처리
    // -----------------------------------------------------------------------
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

    // -----------------------------------------------------------------------
    // T5-9: usetgtinfo 빈 문자열 → trimToNull → targetInfo는 null
    // -----------------------------------------------------------------------
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
