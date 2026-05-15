package dev.jazzybyte.onseoul.notification.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class NotificationTemplateTest {

    @Test
    @DisplayName("render() - title에 serviceId가 포함되고 TemplateSource.FALLBACK을 반환한다")
    void render_containsServiceIdAndFallbackSource() {
        NotificationTemplateRequest req = new NotificationTemplateRequest(
                "SVC-123", "CHANGED", "status", "예약가능", "마감");

        TemplateResult result = NotificationTemplate.render(req);

        assertThat(result.source()).isEqualTo(TemplateSource.FALLBACK);
        assertThat(result.title()).contains("SVC-123");
        assertThat(result.body()).contains("status");
        assertThat(result.body()).contains("예약가능");
        assertThat(result.body()).contains("마감");
    }

    @Test
    @DisplayName("render() - title은 '[서울공공서비스]' 접두사로 시작한다")
    void render_titleStartsWithPrefix() {
        NotificationTemplateRequest req = new NotificationTemplateRequest(
                "SVC-999", "CHANGED", "name", "구장A", "구장B");

        TemplateResult result = NotificationTemplate.render(req);

        assertThat(result.title()).startsWith("[서울공공서비스]");
    }

    @Test
    @DisplayName("render() - body에 → 구분자와 oldValue, newValue가 순서대로 포함된다")
    void render_bodyContainsArrowSeparatorWithOldAndNewValue() {
        NotificationTemplateRequest req = new NotificationTemplateRequest(
                "SVC-001", "CHANGED", "date", "1월 10일", "2월 3일");

        TemplateResult result = NotificationTemplate.render(req);

        assertThat(result.body()).contains("1월 10일");
        assertThat(result.body()).contains("→");
        assertThat(result.body()).contains("2월 3일");
        // oldValue가 newValue보다 앞에 나온다
        int oldIdx = result.body().indexOf("1월 10일");
        int newIdx = result.body().indexOf("2월 3일");
        assertThat(oldIdx).isLessThan(newIdx);
    }
}
