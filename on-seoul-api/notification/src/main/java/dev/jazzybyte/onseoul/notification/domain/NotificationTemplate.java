package dev.jazzybyte.onseoul.notification.domain;

public class NotificationTemplate {

    private NotificationTemplate() {}

    /** Fallback: 정형 변수 치환. AI 호출 실패 시 사용. */
    public static TemplateResult render(NotificationTemplateRequest req) {
        String title = "[서울공공서비스] " + req.serviceId() + " 변경 알림";
        String body  = req.fieldName() + " 이(가) "
                + req.oldValue() + " → " + req.newValue() + " 으로 변경되었습니다.";
        return new TemplateResult(title, body, TemplateSource.FALLBACK);
    }
}
