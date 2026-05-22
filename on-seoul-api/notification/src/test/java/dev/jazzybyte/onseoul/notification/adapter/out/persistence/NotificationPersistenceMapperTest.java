package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * SubscriptionFilter JSONB 파싱 단위 테스트 (ADR-0004 §SubscriptionFilter 도입).
 *
 * <p>NotificationPersistenceMapper.parse는 SubscriptionFilterParserPort 구현으로,
 * 어떤 입력에서도 예외를 던지지 말고 안전한 empty() fallback을 반환해야 한다.
 */
class NotificationPersistenceMapperTest {

    private final NotificationPersistenceMapper mapper = new NotificationPersistenceMapper();

    // ── 정상 파싱 ─────────────────────────────────────────────

    @Test
    @DisplayName("statuses/areaNames/maxClassNames 세 키 모두 파싱된다")
    void parse_allThreeKeys() {
        String json = "{\"statuses\":[\"RECEIVING\",\"CLOSED\"]," +
                      "\"areaNames\":[\"강남구\"]," +
                      "\"maxClassNames\":[\"문화행사\",\"체육시설\"]}";

        SubscriptionFilter f = mapper.parse(json);

        assertThat(f.statuses()).containsExactlyInAnyOrder("RECEIVING", "CLOSED");
        assertThat(f.areaNames()).containsExactly("강남구");
        assertThat(f.maxClassNames()).containsExactlyInAnyOrder("문화행사", "체육시설");
    }

    @Test
    @DisplayName("일부 키만 존재하면 나머지 필드는 빈 Set")
    void parse_partialKeys() {
        String json = "{\"statuses\":[\"RECEIVING\"]}";

        SubscriptionFilter f = mapper.parse(json);

        assertThat(f.statuses()).containsExactly("RECEIVING");
        assertThat(f.areaNames()).isEmpty();
        assertThat(f.maxClassNames()).isEmpty();
    }

    @Test
    @DisplayName("string이 아닌 배열 요소는 무시되고 blank 값도 skip 된다")
    void parse_nonStringAndBlankElements_skipped() {
        String json = "{\"statuses\":[\"RECEIVING\", null, 123, \"\", \"  \", \"CLOSED\"]}";

        SubscriptionFilter f = mapper.parse(json);

        assertThat(f.statuses()).containsExactlyInAnyOrder("RECEIVING", "CLOSED");
    }

    // ── 폴백 시나리오 ─────────────────────────────────────────

    @Test
    @DisplayName("null 입력 → empty filter")
    void parse_null_returnsEmpty() {
        assertThat(mapper.parse(null).isEmpty()).isTrue();
    }

    @Test
    @DisplayName("빈 문자열 입력 → empty filter")
    void parse_blank_returnsEmpty() {
        assertThat(mapper.parse("").isEmpty()).isTrue();
        assertThat(mapper.parse("   ").isEmpty()).isTrue();
    }

    @Test
    @DisplayName("빈 JSON 객체 {} → empty filter")
    void parse_emptyObject_returnsEmpty() {
        assertThat(mapper.parse("{}").isEmpty()).isTrue();
    }

    @Test
    @DisplayName("JSON이 객체가 아닐 때(array/string) → empty filter")
    void parse_nonObjectJson_returnsEmpty() {
        assertThat(mapper.parse("[]").isEmpty()).isTrue();
        assertThat(mapper.parse("\"hello\"").isEmpty()).isTrue();
        assertThat(mapper.parse("42").isEmpty()).isTrue();
    }

    @Test
    @DisplayName("잘못된 JSON 문법 → 예외 대신 empty filter fallback")
    void parse_invalidJson_returnsEmpty() {
        assertThat(mapper.parse("{not valid").isEmpty()).isTrue();
        assertThat(mapper.parse("{\"statuses\":").isEmpty()).isTrue();
    }

    @Test
    @DisplayName("키 값이 배열이 아닐 때(string/object) 해당 키는 빈 Set")
    void parse_nonArrayValue_emptySetForThatKey() {
        SubscriptionFilter f = mapper.parse(
                "{\"statuses\":\"RECEIVING\",\"areaNames\":{\"k\":\"v\"},\"maxClassNames\":[]}");

        assertThat(f.statuses()).isEmpty();
        assertThat(f.areaNames()).isEmpty();
        assertThat(f.maxClassNames()).isEmpty();
    }

    @Test
    @DisplayName("parse(...)는 port 구현으로 동일 입력에 동일 결과를 반환한다")
    void parsePort_deterministicResult() {
        String json = "{\"statuses\":[\"RECEIVING\"]}";

        SubscriptionFilter first = mapper.parse(json);
        SubscriptionFilter second = mapper.parse(json);

        assertThat(first).isEqualTo(second);
    }
}
