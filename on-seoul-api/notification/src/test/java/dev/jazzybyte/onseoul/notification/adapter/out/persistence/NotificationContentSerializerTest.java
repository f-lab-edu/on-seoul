package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.notification.domain.NotificationContent;
import dev.jazzybyte.onseoul.notification.port.out.NotificationContentSerializerPort;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * {@link NotificationContentSerializer} 단위 테스트.
 *
 * <p>핵심 회귀: 직렬화 ↔ 역직렬화 라운드트립 동치성과, 호출자가 평문 폴백으로 분기하도록
 * null/blank/파싱 실패 시 null을 반환하는 방어 경로를 검증한다.
 */
class NotificationContentSerializerTest {

    private final NotificationContentSerializerPort serializer =
            new NotificationContentSerializer(new ObjectMapper());

    @Test
    @DisplayName("라운드트립: 서비스 카드/체인지라인이 직렬화→역직렬화 후 동치로 보존된다")
    void roundTrip_preservesServiceCardsAndChangeLines() {
        NotificationContent original = new NotificationContent(
                "구독하신 2개 서비스 변경 알림",
                "구독하신 2개 서비스에 변경이 감지되었습니다.",
                List.of(
                        new NotificationContent.ServiceCard(
                                "OA-2269", "강남 수영교실", "예약마감", "강남구", "강남센터", "성인",
                                "2026-05-01", "2026-05-31",
                                "https://ex.com/1", "https://ex.com/img.png",
                                List.of(
                                        new NotificationContent.ChangeLine("모집상태", "접수중", "예약마감"),
                                        new NotificationContent.ChangeLine("접수 마감일", "2026-05-20", "2026-05-31"))),
                        new NotificationContent.ServiceCard(
                                "OA-3000", "서초 도서관 대관", "접수중", "서초구", null, null,
                                null, null, "https://ex.com/2", null,
                                List.of())));

        String json = serializer.serialize(original);
        NotificationContent restored = serializer.deserialize(json);

        // record는 deep equals — 카드/체인지라인까지 동치 비교가 그대로 성립한다.
        assertThat(restored).isEqualTo(original);
        // cross-trigger dedup 선조회가 매칭할 수 있도록 serviceId 가 services[] 안에 camelCase 로 직렬화된다.
        assertThat(json).contains("\"serviceId\":\"OA-2269\"");
    }

    @Test
    @DisplayName("services가 빈 콘텐츠도 라운드트립으로 보존된다 (services = [])")
    void roundTrip_emptyServices_preserved() {
        NotificationContent original = new NotificationContent("제목", "요약", List.of());

        NotificationContent restored = serializer.deserialize(serializer.serialize(original));

        assertThat(restored).isEqualTo(original);
        assertThat(restored.services()).isEmpty();
    }

    @Test
    @DisplayName("재발송 호환: serviceId 없는 구버전 payload 도 역직렬화된다 (Jackson missing→null)")
    void deserialize_legacyPayloadWithoutServiceId_restoresWithNullServiceId() {
        // migration 12 이전에 저장된 payload — services[] 객체에 serviceId 키가 없다.
        // 재발송 경로(dispatch_id로 단건 읽기)가 깨지지 않아야 한다.
        String legacyJson = """
                {"title":"제목","summary":"요약","services":[
                  {"name":"강남 수영교실","status":"예약마감","area":"강남구","place":"강남센터",
                   "target":"성인","receiptStart":"2026-05-01","receiptEnd":"2026-05-31",
                   "url":"https://ex.com/1","imageUrl":"https://ex.com/img.png",
                   "changes":[{"label":"모집상태","oldValue":"접수중","newValue":"예약마감"}]}]}
                """;

        NotificationContent restored = serializer.deserialize(legacyJson);

        assertThat(restored).isNotNull();
        assertThat(restored.services()).hasSize(1);
        NotificationContent.ServiceCard card = restored.services().get(0);
        // serviceId 누락 → null. 나머지 필드는 정상 복원.
        assertThat(card.serviceId()).isNull();
        assertThat(card.name()).isEqualTo("강남 수영교실");
        assertThat(card.status()).isEqualTo("예약마감");
        assertThat(card.changes()).hasSize(1);
    }

    @Test
    @DisplayName("신버전 round-trip: serviceId null 카드도 직렬화→역직렬화 보존")
    void roundTrip_nullServiceId_preserved() {
        NotificationContent original = new NotificationContent(
                "제목", "요약",
                List.of(new NotificationContent.ServiceCard(
                        null, "행사", "접수중", null, null, null, null, null, null, null,
                        List.of())));

        NotificationContent restored = serializer.deserialize(serializer.serialize(original));

        assertThat(restored).isEqualTo(original);
        assertThat(restored.services().get(0).serviceId()).isNull();
    }

    @Test
    @DisplayName("serialize(null) → null (페이로드 저장 실패가 발송을 막지 않는다)")
    void serialize_null_returnsNull() {
        assertThat(serializer.serialize(null)).isNull();
    }

    @Test
    @DisplayName("deserialize(null/blank) → null (호출자는 평문 폴백으로 분기)")
    void deserialize_nullOrBlank_returnsNull() {
        assertThat(serializer.deserialize(null)).isNull();
        assertThat(serializer.deserialize("")).isNull();
        assertThat(serializer.deserialize("   ")).isNull();
    }

    @Test
    @DisplayName("deserialize 파싱 실패(깨진 JSON) → null 반환, 예외 전파하지 않음 (평문 폴백)")
    void deserialize_malformedJson_returnsNullWithoutThrowing() {
        assertThat(serializer.deserialize("{not valid json")).isNull();
        assertThat(serializer.deserialize("12345")).isNull();
        assertThat(serializer.deserialize("[\"array-not-object\"]")).isNull();
    }
}
