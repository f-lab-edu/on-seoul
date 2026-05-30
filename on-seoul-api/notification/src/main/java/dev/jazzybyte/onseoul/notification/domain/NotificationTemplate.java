package dev.jazzybyte.onseoul.notification.domain;

import java.util.List;

public class NotificationTemplate {

    private NotificationTemplate() {}

    /**
     * Fallback: 정형 변수 치환. AI 호출 실패 시 사용한다.
     * 배치 변경 목록을 받아 첫 번째 변경을 기준으로 요약 메시지를 만든다.
     * 변경이 비어 있으면(이 경로는 호출자가 사전 차단해야 함) 일반 안내문을 반환한다.
     */
    public static TemplateResult render(NotificationTemplateRequest req) {
        String title = "[서울공공서비스] " + req.serviceId() + " 변경 알림";

        List<NotificationTemplateRequest.ChangeItem> changes = req.changes();
        String body;
        if (changes.isEmpty()) {
            body = "구독하신 서비스에 변경이 감지되었습니다.";
        } else if (changes.size() == 1) {
            NotificationTemplateRequest.ChangeItem c = changes.get(0);
            body = c.fieldName() + " 이(가) "
                    + c.oldValue() + " → " + c.newValue() + " 으로 변경되었습니다.";
        } else {
            NotificationTemplateRequest.ChangeItem c = changes.get(0);
            body = c.fieldName() + " 이(가) "
                    + c.oldValue() + " → " + c.newValue() + " 으로 변경되었습니다. "
                    + "(외 " + (changes.size() - 1) + "건)";
        }
        return new TemplateResult(title, body, TemplateSource.FALLBACK);
    }
}
