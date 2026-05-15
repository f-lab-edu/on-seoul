package dev.jazzybyte.onseoul.notification.adapter.out.fcm;

import com.google.firebase.messaging.FirebaseMessaging;
import com.google.firebase.messaging.Message;
import com.google.firebase.messaging.Notification;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;

import java.util.Optional;

@Slf4j
@Component
class FcmPushAdapter implements PushNotificationPort {

    private final FirebaseMessaging firebaseMessaging;

    FcmPushAdapter(Optional<FirebaseMessaging> firebaseMessaging) {
        this.firebaseMessaging = firebaseMessaging.orElse(null);
    }

    @Override
    public void push(String fcmToken, String title, String body, Long dispatchId) {
        if (firebaseMessaging == null) {
            log.warn("[FCM] Firebase 미초기화, 푸시 스킵: dispatchId={}", dispatchId);
            return;
        }
        try {
            Message message = Message.builder()
                    .setToken(fcmToken)
                    .setNotification(Notification.builder()
                            .setTitle(title)
                            .setBody(body)
                            .build())
                    .putData("dispatchId", String.valueOf(dispatchId))
                    .build();
            String messageId = firebaseMessaging.send(message);
            log.info("[FCM] 푸시 발송 성공: dispatchId={}, messageId={}", dispatchId, messageId);
        } catch (Exception e) {
            log.error("[FCM] 푸시 발송 실패: dispatchId={}, error={}", dispatchId, e.getMessage(), e);
            throw new RuntimeException("FCM 푸시 발송 실패: " + e.getMessage(), e);
        }
    }
}
