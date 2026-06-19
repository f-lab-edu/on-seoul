package dev.jazzybyte.onseoul.notification;

import dev.jazzybyte.onseoul.chat.port.out.DeleteChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatRoomPort;
import dev.jazzybyte.onseoul.collection.port.in.CollectDatasetUseCase;
import dev.jazzybyte.onseoul.collection.port.out.EmbeddingSyncPort;
import dev.jazzybyte.onseoul.collection.port.out.GeocodingPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadApiSourceCatalogPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadChangedServiceIdsPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.collection.port.out.SaveCollectionHistoryPort;
import dev.jazzybyte.onseoul.collection.port.out.SavePublicServicePort;
import dev.jazzybyte.onseoul.collection.port.out.SaveServiceChangeLogPort;
import dev.jazzybyte.onseoul.collection.port.out.SeoulDatasetFetchPort;
import dev.jazzybyte.onseoul.notification.application.NotificationScheduler;
import dev.jazzybyte.onseoul.notification.application.ScheduledTriggerScheduler;
import dev.jazzybyte.onseoul.notification.port.out.LoadBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadScheduledTriggerPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import dev.jazzybyte.onseoul.notification.port.out.SubscriptionFilterParserPort;
import dev.jazzybyte.onseoul.user.port.in.GetMeUseCase;
import dev.jazzybyte.onseoul.user.port.in.LogoutUseCase;
import dev.jazzybyte.onseoul.user.port.in.RefreshTokenUseCase;
import dev.jazzybyte.onseoul.user.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.user.port.out.RefreshTokenStorePort;
import dev.jazzybyte.onseoul.user.port.out.SaveUserPort;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.hamcrest.Matchers.aMapWithSize;
import static org.hamcrest.Matchers.allOf;
import static org.hamcrest.Matchers.hasKey;
import static org.hamcrest.Matchers.not;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 알림 배치 관리 API 컨트롤러 테스트.
 * 두 스케줄러를 MockitoBean으로 대체해 실제 LLM/외부 발송 없이 컨트롤러 → 스케줄러 위임만 확인한다.
 * 인증은 tokenIssuerPort.generateAccessToken 으로 발급한 Bearer 토큰을 사용한다.
 */
@SpringBootTest
@AutoConfigureMockMvc
@TestPropertySource(properties = {
        "spring.autoconfigure.exclude=" +
        "org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration," +
        "org.springframework.boot.autoconfigure.orm.jpa.HibernateJpaAutoConfiguration," +
        "org.springframework.boot.autoconfigure.data.redis.RedisAutoConfiguration," +
        "org.springframework.boot.autoconfigure.data.redis.RedisReactiveAutoConfiguration",
        "jwt.secret=dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=",
        "spring.security.oauth2.client.registration.google.client-id=test",
        "spring.security.oauth2.client.registration.google.client-secret=test",
        "spring.security.oauth2.client.registration.google.scope=openid,email,profile",
        "seoul.api.key=test",
        "kakao.api.key=test",
        "ai.service.url=http://localhost:8000",
        "ai.service.stream-timeout-seconds=120",
        "app.cookie-signing-key=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "app.cors.allowed-origins=http://localhost:3000",
        "knock.api-key=test-knock-key",
        "app.encryption.aes-key=0102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f20",
        "app.encryption.blind-idx-key=a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2"
})
class NotificationBatchAdminControllerTest {

    @Autowired private MockMvc mockMvc;
    @Autowired private dev.jazzybyte.onseoul.user.port.out.TokenIssuerPort tokenIssuerPort;

    @MockitoBean private NotificationScheduler notificationScheduler;
    @MockitoBean private ScheduledTriggerScheduler scheduledTriggerScheduler;

    // SecurityConfigTest와 동일하게 전 모듈 outbound 포트를 mock 처리
    @MockitoBean LoadUserPort loadUserPort;
    @MockitoBean SaveUserPort saveUserPort;
    @MockitoBean RefreshTokenStorePort refreshTokenStorePort;
    @MockitoBean LoadPublicServicePort loadPublicServicePort;
    @MockitoBean SavePublicServicePort savePublicServicePort;
    @MockitoBean LoadApiSourceCatalogPort loadApiSourceCatalogPort;
    @MockitoBean SaveCollectionHistoryPort saveCollectionHistoryPort;
    @MockitoBean SaveServiceChangeLogPort saveServiceChangeLogPort;
    @MockitoBean LoadChangedServiceIdsPort loadChangedServiceIdsPort;
    @MockitoBean EmbeddingSyncPort embeddingSyncPort;
    @MockitoBean SeoulDatasetFetchPort seoulDatasetFetchPort;
    @MockitoBean GeocodingPort geocodingPort;
    @MockitoBean StringRedisTemplate stringRedisTemplate;
    @MockitoBean CollectDatasetUseCase collectDatasetUseCase;
    @MockitoBean RefreshTokenUseCase refreshTokenUseCase;
    @MockitoBean LogoutUseCase logoutUseCase;
    @MockitoBean GetMeUseCase getMeUseCase;
    @MockitoBean SaveChatRoomPort saveChatRoomPort;
    @MockitoBean LoadChatRoomPort loadChatRoomPort;
    @MockitoBean SaveChatMessagePort saveChatMessagePort;
    @MockitoBean LoadChatMessagePort loadChatMessagePort;
    @MockitoBean DeleteChatRoomPort deleteChatRoomPort;
    @MockitoBean LoadSubscriptionPort loadSubscriptionPort;
    @MockitoBean SaveSubscriptionPort saveSubscriptionPort;
    @MockitoBean SaveDispatchPort saveDispatchPort;
    @MockitoBean LoadDispatchPort loadDispatchPort;
    @MockitoBean LoadServiceChangePort loadServiceChangePort;
    @MockitoBean LoadScheduledTriggerPort loadScheduledTriggerPort;
    @MockitoBean SaveBatchPort saveBatchPort;
    @MockitoBean LoadBatchPort loadBatchPort;
    @MockitoBean SubscriptionFilterParserPort subscriptionFilterParserPort;
    @MockitoBean LoadUserContactPort loadUserContactPort;

    @Test
    @DisplayName("POST /internal/notifications/batch/change — 인증된 사용자 200, CHANGE 배치 실행")
    void runChange_authenticated_returns200() throws Exception {
        when(notificationScheduler.runManually())
                .thenReturn(NotificationScheduler.ManualRunResult.RAN);

        mockMvc.perform(post("/internal/notifications/batch/change")
                        .header("Authorization", "Bearer " + tokenIssuerPort.generateAccessToken(1L)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.batch").value("CHANGE"))
                .andExpect(jsonPath("$.status").value("RAN"));

        verify(notificationScheduler).runManually();
    }

    @Test
    @DisplayName("POST /internal/notifications/batch/scheduled — 인증된 사용자 200, 시점 배치 실행")
    void runScheduled_authenticated_returns200() throws Exception {
        when(scheduledTriggerScheduler.runManually())
                .thenReturn(ScheduledTriggerScheduler.ManualRunResult.RAN);

        mockMvc.perform(post("/internal/notifications/batch/scheduled")
                        .header("Authorization", "Bearer " + tokenIssuerPort.generateAccessToken(1L)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.batch").value("SCHEDULED"))
                .andExpect(jsonPath("$.status").value("RAN"));

        verify(scheduledTriggerScheduler).runManually();
    }

    @Test
    @DisplayName("POST /internal/notifications/batch/change — 이미 실행 중이면 409")
    void runChange_alreadyRunning_returns409() throws Exception {
        when(notificationScheduler.runManually())
                .thenReturn(NotificationScheduler.ManualRunResult.SKIPPED_ALREADY_RUNNING);

        mockMvc.perform(post("/internal/notifications/batch/change")
                        .header("Authorization", "Bearer " + tokenIssuerPort.generateAccessToken(1L)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.status").value("SKIPPED_ALREADY_RUNNING"));
    }

    @Test
    @DisplayName("POST /internal/notifications/batch/all — CHANGE→SCHEDULED 순서로 둘 다 실행, 200")
    void runAll_authenticated_returns200() throws Exception {
        when(notificationScheduler.runManually())
                .thenReturn(NotificationScheduler.ManualRunResult.RAN);
        when(scheduledTriggerScheduler.runManually())
                .thenReturn(ScheduledTriggerScheduler.ManualRunResult.RAN);

        mockMvc.perform(post("/internal/notifications/batch/all")
                        .header("Authorization", "Bearer " + tokenIssuerPort.generateAccessToken(1L)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.change.batch").value("CHANGE"))
                .andExpect(jsonPath("$.change.status").value("RAN"))
                .andExpect(jsonPath("$.scheduled.batch").value("SCHEDULED"))
                .andExpect(jsonPath("$.scheduled.status").value("RAN"));

        var inOrder = org.mockito.Mockito.inOrder(notificationScheduler, scheduledTriggerScheduler);
        inOrder.verify(notificationScheduler).runManually();
        inOrder.verify(scheduledTriggerScheduler).runManually();
    }

    @Test
    @DisplayName("POST /internal/notifications/batch/scheduled — 이미 실행 중이면 409")
    void runScheduled_alreadyRunning_returns409() throws Exception {
        when(scheduledTriggerScheduler.runManually())
                .thenReturn(ScheduledTriggerScheduler.ManualRunResult.SKIPPED_ALREADY_RUNNING);

        mockMvc.perform(post("/internal/notifications/batch/scheduled")
                        .header("Authorization", "Bearer " + tokenIssuerPort.generateAccessToken(1L)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.batch").value("SCHEDULED"))
                .andExpect(jsonPath("$.status").value("SKIPPED_ALREADY_RUNNING"));
    }

    @Test
    @DisplayName("POST /internal/notifications/batch/all — CHANGE는 실행되나 SCHEDULED가 이미 실행 중이면 부분 SKIPPED, 200 유지")
    void runAll_scheduledSkipped_returns200WithMixedStatus() throws Exception {
        when(notificationScheduler.runManually())
                .thenReturn(NotificationScheduler.ManualRunResult.RAN);
        when(scheduledTriggerScheduler.runManually())
                .thenReturn(ScheduledTriggerScheduler.ManualRunResult.SKIPPED_ALREADY_RUNNING);

        mockMvc.perform(post("/internal/notifications/batch/all")
                        .header("Authorization", "Bearer " + tokenIssuerPort.generateAccessToken(1L)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.change.status").value("RAN"))
                .andExpect(jsonPath("$.scheduled.status").value("SKIPPED_ALREADY_RUNNING"));
    }

    // --- PII/시크릿 비노출: 응답 바디에 batch+status 외 필드가 없어야 한다 ---

    @Test
    @DisplayName("단일 배치 응답 바디는 정확히 {batch,status} 두 키만 가진다 (PII/내부정보 비노출)")
    void singleResponse_exposesOnlyBatchAndStatus() throws Exception {
        when(notificationScheduler.runManually())
                .thenReturn(NotificationScheduler.ManualRunResult.RAN);

        mockMvc.perform(post("/internal/notifications/batch/change")
                        .header("Authorization", "Bearer " + tokenIssuerPort.generateAccessToken(1L)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$", aMapWithSize(2)))
                .andExpect(jsonPath("$", allOf(hasKey("batch"), hasKey("status"))))
                .andExpect(jsonPath("$", not(hasKey("contact"))))
                .andExpect(jsonPath("$", not(hasKey("phone"))))
                .andExpect(jsonPath("$", not(hasKey("email"))))
                .andExpect(jsonPath("$", not(hasKey("serviceId"))))
                .andExpect(jsonPath("$", not(hasKey("sentCount"))))
                .andExpect(jsonPath("$", not(hasKey("batchId"))));
    }

    @Test
    @DisplayName("all 응답 바디는 정확히 {change,scheduled} 두 키, 각 하위는 {batch,status}만 (PII 비노출)")
    void allResponse_exposesOnlyBatchAndStatusPerEntry() throws Exception {
        when(notificationScheduler.runManually())
                .thenReturn(NotificationScheduler.ManualRunResult.RAN);
        when(scheduledTriggerScheduler.runManually())
                .thenReturn(ScheduledTriggerScheduler.ManualRunResult.RAN);

        mockMvc.perform(post("/internal/notifications/batch/all")
                        .header("Authorization", "Bearer " + tokenIssuerPort.generateAccessToken(1L)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$", aMapWithSize(2)))
                .andExpect(jsonPath("$", allOf(hasKey("change"), hasKey("scheduled"))))
                .andExpect(jsonPath("$.change", aMapWithSize(2)))
                .andExpect(jsonPath("$.change", allOf(hasKey("batch"), hasKey("status"))))
                .andExpect(jsonPath("$.scheduled", aMapWithSize(2)))
                .andExpect(jsonPath("$.scheduled", allOf(hasKey("batch"), hasKey("status"))));
    }

    // --- permitAll 범위(보안 핵심): batch/** 만 열리고 다른 보호 경로는 401 ---

    @Test
    @DisplayName("보안: 보호 경로 /admin/collection/trigger 는 인증 없이 여전히 401 (permitAll 누수 없음)")
    void protectedPath_collectionTrigger_stillUnauthorized() throws Exception {
        mockMvc.perform(post("/admin/collection/trigger"))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(collectDatasetUseCase);
    }

    @Test
    @DisplayName("보안: 임의 인증 필요 경로 /api/users/me 는 인증 없이 401")
    void protectedPath_usersMe_stillUnauthorized() throws Exception {
        mockMvc.perform(get("/api/users/me"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("보안: permitAll 은 /internal/notifications/batch 접두사로 새지 않는다 — 유사 경로는 401")
    void permitAll_doesNotLeakToLookalikePaths() throws Exception {
        // 접두사만 같고 batch/** 매칭 밖인 경로는 보호되어야 한다.
        mockMvc.perform(post("/internal/notifications/other"))
                .andExpect(status().isUnauthorized());
        mockMvc.perform(post("/internal/something"))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(notificationScheduler, scheduledTriggerScheduler);
    }
}
