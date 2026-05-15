package dev.jazzybyte.onseoul.notification.port.out;

public interface PushNotificationPort {

    /**
     * FCM 푸시 알림을 발송한다.
     *
     * @param fcmToken   수신 기기의 FCM 등록 토큰
     * @param title      알림 제목
     * @param body       알림 본문
     * @param dispatchId dispatch 레코드 ID (FCM idempotency key로 사용)
     * @throws RuntimeException FCM 발송 실패 시. 호출자는 발송 실패 상태를 기록해야 한다.
     */
    void push(String fcmToken, String title, String body, Long dispatchId);
}
