package dev.jazzybyte.onseoul.chat.adapter.out.agent;

import dev.jazzybyte.onseoul.chat.domain.PrevEntity;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class ServiceCardParserTest {

    private final ServiceCardParser parser = new ServiceCardParser();

    @Test
    @DisplayName("parsePrevEntities() — service_cards 배열에서 service_id + service_name(label)을 순서 그대로 추출한다")
    void parse_extractsIdAndLabelInOrder() {
        String json = "[{\"service_id\":\"S1\",\"service_name\":\"강남 음악회 🎵\",\"service_status\":\"접수중\"},"
                + "{\"service_id\":\"S2\",\"service_name\":\"미술 전시\",\"place_name\":\"강남구민회관\"}]";

        List<PrevEntity> result = parser.parsePrevEntities(json, 10);

        assertThat(result).containsExactly(
                new PrevEntity("S1", "강남 음악회 🎵"),
                new PrevEntity("S2", "미술 전시"));
    }

    @Test
    @DisplayName("parsePrevEntities() — service_name이 null이면 label=\"\"로 정규화하되 카드 자리는 유지한다")
    void parse_nullServiceName_labelEmptyButKept() {
        String json = "[{\"service_id\":\"S1\",\"service_name\":null},"
                + "{\"service_id\":\"S2\",\"service_name\":\"전시\"}]";

        List<PrevEntity> result = parser.parsePrevEntities(json, 10);

        assertThat(result).containsExactly(
                new PrevEntity("S1", ""),
                new PrevEntity("S2", "전시"));
    }

    @Test
    @DisplayName("parsePrevEntities() — service_name 키가 아예 없어도 label=\"\"로 카드 유지")
    void parse_missingServiceNameKey_labelEmpty() {
        String json = "[{\"service_id\":\"S1\"}]";

        List<PrevEntity> result = parser.parsePrevEntities(json, 10);

        assertThat(result).containsExactly(new PrevEntity("S1", ""));
    }

    @Test
    @DisplayName("parsePrevEntities() — 사실 필드(상태/접수일/장소 등)는 추출 결과에 포함되지 않는다(serviceId+label만)")
    void parse_onlyIdAndLabel_noFactFields() {
        String json = "[{\"service_id\":\"S1\",\"service_name\":\"행사\",\"service_status\":\"접수중\","
                + "\"receipt_start_dt\":\"2026-06-01\",\"place_name\":\"회관\",\"payment_type\":\"무료\"}]";

        List<PrevEntity> result = parser.parsePrevEntities(json, 10);

        assertThat(result).containsExactly(new PrevEntity("S1", "행사"));
    }

    @Test
    @DisplayName("parsePrevEntities() — 10건 초과면 앞 10건만 유지(cap)")
    void parse_moreThanTen_capsToFirstTen() {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < 15; i++) {
            if (i > 0) sb.append(",");
            sb.append("{\"service_id\":\"S").append(i).append("\",\"service_name\":\"n").append(i).append("\"}");
        }
        sb.append("]");

        List<PrevEntity> result = parser.parsePrevEntities(sb.toString(), 10);

        assertThat(result).hasSize(10);
        assertThat(result.get(0)).isEqualTo(new PrevEntity("S0", "n0"));
        assertThat(result.get(9)).isEqualTo(new PrevEntity("S9", "n9"));
    }

    @Test
    @DisplayName("parsePrevEntities() — service_id가 비어 있는 카드는 건너뛴다(바인딩 불가)")
    void parse_blankServiceId_skipped() {
        String json = "[{\"service_id\":\"\",\"service_name\":\"a\"},"
                + "{\"service_name\":\"b\"},"
                + "{\"service_id\":\"S3\",\"service_name\":\"c\"}]";

        List<PrevEntity> result = parser.parsePrevEntities(json, 10);

        assertThat(result).containsExactly(new PrevEntity("S3", "c"));
    }

    @Test
    @DisplayName("parsePrevEntities() — service_id가 JSON 숫자여도 문자열로 정규화되어 카드가 유지된다(asText 강제)")
    void parse_numericServiceId_coercedToString() {
        String json = "[{\"service_id\":123,\"service_name\":\"행사\"}]";

        List<PrevEntity> result = parser.parsePrevEntities(json, 10);

        assertThat(result).containsExactly(new PrevEntity("123", "행사"));
    }

    @Test
    @DisplayName("parsePrevEntities() — service_id가 명시적 null인 카드는 건너뛴다(빈 service_id와 동일 처리)")
    void parse_nullServiceId_skipped() {
        String json = "[{\"service_id\":null,\"service_name\":\"a\"},"
                + "{\"service_id\":\"S2\",\"service_name\":\"b\"}]";

        List<PrevEntity> result = parser.parsePrevEntities(json, 10);

        assertThat(result).containsExactly(new PrevEntity("S2", "b"));
    }

    @Test
    @DisplayName("parsePrevEntities() — limit=0이면 빈 리스트(cap 경계)")
    void parse_zeroLimit_returnsEmpty() {
        String json = "[{\"service_id\":\"S1\",\"service_name\":\"a\"}]";

        assertThat(parser.parsePrevEntities(json, 0)).isEmpty();
    }

    @Test
    @DisplayName("parsePrevEntities() — service_id 없는 카드(제외)가 앞에 섞여도 뒤 카드의 인덱스/순서는 유지된다")
    void parse_skippedCardsDoNotShiftRemainingOrder() {
        // 서수 바인딩 정합성: 빈 service_id 카드는 빠지되, 남는 카드들은 원래 상대 순서를 유지해야 한다.
        String json = "[{\"service_id\":\"\",\"service_name\":\"skip1\"},"
                + "{\"service_id\":\"A\",\"service_name\":\"first\"},"
                + "{\"service_name\":\"skip2\"},"
                + "{\"service_id\":\"B\",\"service_name\":\"second\"}]";

        List<PrevEntity> result = parser.parsePrevEntities(json, 10);

        assertThat(result).containsExactly(
                new PrevEntity("A", "first"),
                new PrevEntity("B", "second"));
    }

    @Test
    @DisplayName("parsePrevEntities() — null/빈문자열/비배열/파싱실패는 빈 리스트")
    void parse_invalidInputs_returnEmpty() {
        assertThat(parser.parsePrevEntities(null, 10)).isEmpty();
        assertThat(parser.parsePrevEntities("", 10)).isEmpty();
        assertThat(parser.parsePrevEntities("  ", 10)).isEmpty();
        assertThat(parser.parsePrevEntities("{\"service_id\":\"S1\"}", 10)).isEmpty(); // 객체(비배열)
        assertThat(parser.parsePrevEntities("not json", 10)).isEmpty();
        assertThat(parser.parsePrevEntities("[]", 10)).isEmpty();
    }
}
