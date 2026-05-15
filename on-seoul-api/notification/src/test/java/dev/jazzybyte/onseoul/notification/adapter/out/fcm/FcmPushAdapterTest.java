package dev.jazzybyte.onseoul.notification.adapter.out.fcm;

import com.google.firebase.messaging.FirebaseMessaging;
import com.google.firebase.messaging.FirebaseMessagingException;
import dev.jazzybyte.onseoul.notification.port.out.PushNotificationPort;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class FcmPushAdapterTest {

    @Test
    @DisplayName("push() - Firebase 미초기화(빈 Optional) 시 예외 없이 스킵된다")
    void push_firebaseNotInitialized_skipsSilently() {
        PushNotificationPort adapter = new FcmPushAdapter(Optional.empty());

        assertThatCode(() -> adapter.push("token-abc", "제목", "본문", 1L))
                .doesNotThrowAnyException();
    }

    @Test
    @DisplayName("push() - FCM send() 실패 시 RuntimeException을 전파한다")
    void push_fcmSendFails_throwsRuntimeException() throws FirebaseMessagingException {
        FirebaseMessaging mockMessaging = mock(FirebaseMessaging.class);
        when(mockMessaging.send(any())).thenThrow(mock(FirebaseMessagingException.class));

        PushNotificationPort adapter = new FcmPushAdapter(Optional.of(mockMessaging));

        assertThatThrownBy(() -> adapter.push("token-xyz", "제목", "본문", 99L))
                .isInstanceOf(RuntimeException.class)
                .hasMessageContaining("FCM 푸시 발송 실패");
    }

    @Test
    @DisplayName("push() - FCM send() 성공 시 예외 없이 완료된다")
    void push_success_noException() throws FirebaseMessagingException {
        FirebaseMessaging mockMessaging = mock(FirebaseMessaging.class);
        when(mockMessaging.send(any())).thenReturn("projects/test/messages/123");

        PushNotificationPort adapter = new FcmPushAdapter(Optional.of(mockMessaging));

        assertThatCode(() -> adapter.push("token-ok", "제목", "본문", 42L))
                .doesNotThrowAnyException();
    }
}
