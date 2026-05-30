package dev.jazzybyte.onseoul.notification.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class NotificationTemplateTest {

    private NotificationTemplateRequest singleChange(String serviceId, String field,
                                                    String oldVal, String newVal) {
        return new NotificationTemplateRequest(serviceId, List.of(
                new NotificationTemplateRequest.ChangeItem("UPDATED", field, oldVal, newVal)));
    }

    @Test
    @DisplayName("render() - title에 serviceId가 포함되고 TemplateSource.FALLBACK을 반환한다")
    void render_containsServiceIdAndFallbackSource() {
        TemplateResult result = NotificationTemplate.render(
                singleChange("SVC-123", "status", "예약가능", "마감"));

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
        assertThat(result.title()).contains("SVC-123");
        assertThat(result.body()).contains("status");
        assertThat(result.body()).contains("예약가능");
        assertThat(result.body()).contains("마감");
    }

    @Test
    @DisplayName("render() - title은 '[서울공공서비스]' 접두사로 시작한다")
    void render_titleStartsWithPrefix() {
        TemplateResult result = NotificationTemplate.render(
                singleChange("SVC-999", "name", "구장A", "구장B"));

        assertThat(result.title()).startsWith("[서울공공서비스]");
    }

    @Test
    @DisplayName("render() - body에 → 구분자와 oldValue, newValue가 순서대로 포함된다")
    void render_bodyContainsArrowSeparatorWithOldAndNewValue() {
        TemplateResult result = NotificationTemplate.render(
                singleChange("SVC-001", "date", "1월 10일", "2월 3일"));

        assertThat(result.body()).contains("1월 10일");
        assertThat(result.body()).contains("→");
        assertThat(result.body()).contains("2월 3일");
        int oldIdx = result.body().indexOf("1월 10일");
        int newIdx = result.body().indexOf("2월 3일");
        assertThat(oldIdx).isLessThan(newIdx);
    }

    @Test
    @DisplayName("render() - 변경이 2건 이상이면 첫 항목 + '(외 N건)' 요약이 포함된다")
    void render_multipleChanges_summarizes() {
        NotificationTemplateRequest req = new NotificationTemplateRequest("SVC-555", List.of(
                new NotificationTemplateRequest.ChangeItem("UPDATED", "status", "OPEN", "CLOSED"),
                new NotificationTemplateRequest.ChangeItem("UPDATED", "name", "A", "B"),
                new NotificationTemplateRequest.ChangeItem("UPDATED", "place", "P1", "P2")
        ));

        TemplateResult result = NotificationTemplate.render(req);

        assertThat(result.body()).contains("status");
        assertThat(result.body()).contains("외 2건");
    }
}
