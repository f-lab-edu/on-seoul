package dev.jazzybyte.onseoul.notification.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class NotificationTemplateTest {

    private NotificationTemplateRequest.ServiceChangeGroup group(
            String serviceId, String serviceName,
            NotificationTemplateRequest.ChangeItem... changes) {
        return new NotificationTemplateRequest.ServiceChangeGroup(
                serviceId, serviceName, null, null, null, null, null, null, null, null,
                List.of(changes));
    }

    private NotificationTemplateRequest.ChangeItem change(String field, String oldVal, String newVal) {
        return new NotificationTemplateRequest.ChangeItem("UPDATED", field, oldVal, newVal);
    }

    // ── fallback summary: 사실은 Knock 카드가 그리므로 summary는 개수/이름 안내 수준 ──

    @Test
    @DisplayName("render() - 단일 서비스: title/summary에 serviceName이 포함되고 FALLBACK 소스를 반환한다")
    void render_singleService_containsServiceNameAndFallbackSource() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(List.of(
                group("SVC-123", "강남 수영교실", change("serviceStatus", "예약가능", "마감")))));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
        assertThat(result.title()).startsWith("[서울공공서비스]");
        assertThat(result.title()).contains("강남 수영교실");
        assertThat(result.summary()).contains("강남 수영교실");
        assertThat(result.isValid()).isTrue();
    }

    @Test
    @DisplayName("render() - 단일 서비스: serviceName이 없으면 serviceId가 title/summary에 사용된다")
    void render_singleService_fallsBackToServiceIdWhenNoName() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(List.of(
                group("SVC-999", null, change("name", "구장A", "구장B")))));

        assertThat(result.title()).contains("SVC-999");
        assertThat(result.summary()).contains("SVC-999");
    }

    @Test
    @DisplayName("render() - 복수 서비스: title/summary에 'N개 서비스'가 포함된다")
    void render_multipleServices_containsCount() {
        NotificationTemplateRequest req = new NotificationTemplateRequest(List.of(
                group("SVC-A", "행사A", change("serviceStatus", "OPEN", "CLOSED")),
                group("SVC-B", "행사B", change("serviceStatus", "OPEN", "CLOSED")),
                group("SVC-C", "행사C", change("serviceStatus", "OPEN", "CLOSED"))));

        TemplateResult result = NotificationTemplate.render(req);

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
        assertThat(result.title()).contains("3개 서비스");
        assertThat(result.summary()).contains("3개");
    }

    @Test
    @DisplayName("render() - services가 비면 일반 안내문을 반환한다")
    void render_emptyServices_returnsGenericMessage() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(List.of()));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
        assertThat(result.title()).startsWith("[서울공공서비스]");
        assertThat(result.title()).contains("변경");
        assertThat(result.summary()).contains("변경");
    }

    // ── 시점 트리거 fallback 문구 분기 (모델 B) ──────────────────────────────

    @Test
    @DisplayName("render() - OPEN_DAY 트리거는 '서비스 개시' 문구를 사용한다")
    void render_openDay_usesOpeningWording() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(
                TriggerType.OPEN_DAY, List.of(group("SVC-1", "강남 수영교실"))));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
        assertThat(result.title()).contains("서비스 개시");
        assertThat(result.title()).contains("강남 수영교실");
        assertThat(result.summary()).contains("서비스 개시");
    }

    @Test
    @DisplayName("render() - BEFORE_RECEIPT_D1 트리거는 '접수 시작 예정' 문구를 사용한다")
    void render_beforeReceiptD1_usesReceiptStartWording() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(
                TriggerType.BEFORE_RECEIPT_D1, List.of(group("SVC-1", "강남 수영교실"))));

        assertThat(result.title()).contains("접수 시작 예정");
        assertThat(result.summary()).contains("접수 시작 예정");
    }

    @Test
    @DisplayName("render() - DEADLINE_DDAY 트리거는 '접수 마감 임박' 문구를 사용한다")
    void render_deadlineDday_usesDeadlineWording() {
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(
                TriggerType.DEADLINE_DDAY, List.of(group("SVC-1", "강남 수영교실"))));

        assertThat(result.title()).contains("접수 마감 임박");
        assertThat(result.summary()).contains("접수 마감 임박");
    }

    @Test
    @DisplayName("render() - 시점 트리거는 changes 빈 배열(변경 없음)이어도 문구를 만든다")
    void render_scheduledTrigger_emptyChanges_stillRenders() {
        NotificationTemplateRequest.ServiceChangeGroup g =
                new NotificationTemplateRequest.ServiceChangeGroup(
                        "SVC-1", "강남 수영교실", null, null, null, null, null, null, null, null, List.of());
        TemplateResult result = NotificationTemplate.render(new NotificationTemplateRequest(
                TriggerType.DEADLINE_DDAY, List.of(g)));

        assertThat(result.isValid()).isTrue();
        assertThat(result.title()).contains("접수 마감 임박");
    }

    // ── 한글 라벨 매핑 (Knock changes[].label 재사용) ──────────────────────

    @Test
    @DisplayName("fieldLabel() - serviceStatus는 '모집상태'로 매핑된다 (camelCase 미노출)")
    void fieldLabel_serviceStatus_mapsToKorean() {
        assertThat(NotificationTemplate.fieldLabel("serviceStatus")).isEqualTo("모집상태");
        assertThat(NotificationTemplate.fieldLabel("service_status")).isEqualTo("모집상태");
        assertThat(NotificationTemplate.fieldLabel("receiptStartDt")).isEqualTo("접수 시작일");
        assertThat(NotificationTemplate.fieldLabel("receiptEndDt")).isEqualTo("접수 마감일");
    }

    @Test
    @DisplayName("fieldLabel() - 매핑에 없는 field_name은 원본을 그대로 반환한다")
    void fieldLabel_unmapped_keepsRaw() {
        assertThat(NotificationTemplate.fieldLabel("someUnknownField")).isEqualTo("someUnknownField");
    }

    @Test
    @DisplayName("fieldLabel() - null이면 빈 문자열을 반환한다")
    void fieldLabel_null_returnsEmpty() {
        assertThat(NotificationTemplate.fieldLabel(null)).isEmpty();
    }
}
