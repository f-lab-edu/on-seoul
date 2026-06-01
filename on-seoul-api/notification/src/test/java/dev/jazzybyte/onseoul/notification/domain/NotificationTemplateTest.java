package dev.jazzybyte.onseoul.notification.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class NotificationTemplateTest {

    private NotificationTemplateRequest.ServiceChangeGroup group(
            String serviceId, String serviceName, String serviceUrl,
            NotificationTemplateRequest.ChangeItem... changes) {
        return new NotificationTemplateRequest.ServiceChangeGroup(
                serviceId, serviceName, serviceUrl, null, null, null, null, null, null, null,
                List.of(changes));
    }

    private NotificationTemplateRequest.ChangeItem change(String field, String oldVal, String newVal) {
        return new NotificationTemplateRequest.ChangeItem("UPDATED", field, oldVal, newVal);
    }

    private NotificationTemplateRequest.ChangeItem typed(String type, String field, String oldVal, String newVal) {
        return new NotificationTemplateRequest.ChangeItem(type, field, oldVal, newVal);
    }

    private NotificationTemplateRequest singleService(String serviceId, String serviceName,
                                                      String field, String oldVal, String newVal) {
        return new NotificationTemplateRequest(List.of(
                group(serviceId, serviceName, null, change(field, oldVal, newVal))));
    }

    // ── 단일 서비스 ───────────────────────────────────────────────────────

    @Test
    @DisplayName("render() - 단일 서비스: title에 serviceName이 포함되고 FALLBACK 소스를 반환한다")
    void render_singleService_containsServiceNameAndFallbackSource() {
        TemplateResult result = NotificationTemplate.render(
                singleService("SVC-123", "강남 수영교실", "status", "예약가능", "마감"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
        assertThat(result.title()).startsWith("[서울공공서비스]");
        assertThat(result.title()).contains("강남 수영교실");
        assertThat(result.body()).contains("status");
        assertThat(result.body()).contains("예약가능");
        assertThat(result.body()).contains("마감");
    }

    @Test
    @DisplayName("render() - 단일 서비스: serviceName이 없으면 serviceId가 title에 사용된다")
    void render_singleService_fallsBackToServiceIdWhenNoName() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(List.of(
                group("SVC-999", null, null, change("name", "구장A", "구장B")))));

        assertThat(result.title()).contains("SVC-999");
    }

    @Test
    @DisplayName("render() - 단일 서비스: serviceUrl이 있으면 body에 링크 줄이 추가된다")
    void render_singleService_appendsUrlLine() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(List.of(
                group("SVC-1", "행사A", "https://example.com/a", change("status", "OPEN", "CLOSED")))));

        assertThat(result.body()).contains("https://example.com/a");
    }

    @Test
    @DisplayName("render() - 단일 서비스: body에 → 구분자와 oldValue, newValue가 순서대로 포함된다")
    void render_singleService_bodyContainsArrowOrder() {
        TemplateResult result = NotificationTemplate.render(
                singleService("SVC-001", "교육A", "date", "1월 10일", "2월 3일"));

        assertThat(result.body()).contains("1월 10일");
        assertThat(result.body()).contains("→");
        assertThat(result.body()).contains("2월 3일");
        assertThat(result.body().indexOf("1월 10일")).isLessThan(result.body().indexOf("2월 3일"));
    }

    @Test
    @DisplayName("render() - 단일 서비스, 변경 2건 이상이면 첫 항목 + '(외 N건)' 요약")
    void render_singleService_multipleChanges_summarizes() {
        NotificationTemplateRequest req = new NotificationTemplateRequest(List.of(
                group("SVC-555", "체육관", null,
                        change("status", "OPEN", "CLOSED"),
                        change("name", "A", "B"),
                        change("place", "P1", "P2"))));

        TemplateResult result = NotificationTemplate.render(req);

        assertThat(result.body()).contains("status");
        assertThat(result.body()).contains("외 2건");
    }

    // ── 복수 서비스 ───────────────────────────────────────────────────────

    @Test
    @DisplayName("render() - 복수 서비스: title에 'N개 서비스'가 포함되고 각 서비스가 body에 나열된다")
    void render_multipleServices_listsEachService() {
        NotificationTemplateRequest req = new NotificationTemplateRequest(List.of(
                group("SVC-A", "행사A", null, change("status", "OPEN", "CLOSED")),
                group("SVC-B", "행사B", null, change("status", "OPEN", "CLOSED")),
                group("SVC-C", "행사C", null, change("status", "OPEN", "CLOSED"))));

        TemplateResult result = NotificationTemplate.render(req);

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
        assertThat(result.title()).contains("3개 서비스");
        assertThat(result.body()).contains("행사A");
        assertThat(result.body()).contains("행사B");
        assertThat(result.body()).contains("행사C");
    }

    @Test
    @DisplayName("render() - 복수 서비스: 5개 초과 시 상위 5개만 나열되고 '외 M건'이 붙는다")
    void render_multipleServices_truncatesBeyondLimit() {
        List<NotificationTemplateRequest.ServiceChangeGroup> groups = new java.util.ArrayList<>();
        for (int i = 1; i <= 8; i++) {
            groups.add(group("SVC-" + i, "행사" + i, null, change("status", "OPEN", "CLOSED")));
        }
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(groups));

        assertThat(result.title()).contains("8개 서비스");
        assertThat(result.body()).contains("외 3건");
        assertThat(result.body()).contains("행사5");
        assertThat(result.body()).doesNotContain("행사6");
    }

    // ── change_type 분기 ──────────────────────────────────────────────────

    @Test
    @DisplayName("render() - NEW 변경: '신규' 류 문구가 포함되고 'null'이 노출되지 않는다")
    void render_newChange_showsRegisteredAndNoNull() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(List.of(
                group("SVC-N", "신규강좌", null, typed("NEW", null, null, null)))));

        assertThat(result.body()).contains("신규");
        assertThat(result.body()).doesNotContain("null");
    }

    @Test
    @DisplayName("render() - DELETED 변경: '종료' 류 문구가 포함되고 'null'이 노출되지 않는다")
    void render_deletedChange_showsTerminatedAndNoNull() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(List.of(
                group("SVC-D", "폐지강좌", null, typed("DELETED", null, null, null)))));

        assertThat(result.body()).contains("종료");
        assertThat(result.body()).doesNotContain("null");
    }

    @Test
    @DisplayName("render() - change_type이 소문자/공백이어도 분기된다")
    void render_changeType_caseAndWhitespaceInsensitive() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(List.of(
                group("SVC-N2", "강좌", null, typed("  new  ", null, null, null)))));

        assertThat(result.body()).contains("신규");
        assertThat(result.body()).doesNotContain("null");
    }

    @Test
    @DisplayName("render() - change_type이 null이어도 'null' 문구 없이 일반 변경 안내를 낸다")
    void render_nullChangeType_noNullLeak() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(List.of(
                group("SVC-NULL", "강좌", null, typed(null, null, null, null)))));

        assertThat(result.body()).doesNotContain("null");
    }

    // ── field_name / 값 한글 매핑 (UPDATED) ─────────────────────────────────

    @Test
    @DisplayName("render() - UPDATED serviceStatus: field_name이 한글 라벨('모집상태')로 노출된다")
    void render_updatedServiceStatus_koreanFieldLabel() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(List.of(
                group("SVC-S", "수영교실", null, typed("UPDATED", "serviceStatus", "접수중", "예약마감")))));

        assertThat(result.body()).contains("모집상태");
        assertThat(result.body()).doesNotContain("serviceStatus");
        assertThat(result.body()).contains("접수중");
        assertThat(result.body()).contains("예약마감");
    }

    @Test
    @DisplayName("render() - UPDATED: 매핑에 없는 field_name은 원본 그대로 노출된다")
    void render_unmappedFieldName_keepsRaw() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(List.of(
                group("SVC-X", "강좌", null, typed("UPDATED", "someUnknownField", "A", "B")))));

        assertThat(result.body()).contains("someUnknownField");
    }

    // ── 방어 ──────────────────────────────────────────────────────────────

    @Test
    @DisplayName("render() - services가 비면 일반 안내문을 반환한다")
    void render_emptyServices_returnsGenericMessage() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(List.of()));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
        assertThat(result.title()).startsWith("[서울공공서비스]");
        assertThat(result.body()).contains("변경이 감지");
    }
}
