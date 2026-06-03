package dev.jazzybyte.onseoul.security;

import dev.jazzybyte.onseoul.user.adapter.out.jwt.JwtTokenIssuer;
import dev.jazzybyte.onseoul.collection.port.in.CollectDatasetUseCase;
import dev.jazzybyte.onseoul.user.port.in.GetMeUseCase;
import dev.jazzybyte.onseoul.user.port.in.LogoutUseCase;
import dev.jazzybyte.onseoul.user.port.in.RefreshTokenUseCase;
import dev.jazzybyte.onseoul.collection.port.out.GeocodingPort;
import dev.jazzybyte.onseoul.collection.port.out.EmbeddingSyncPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadApiSourceCatalogPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadChangedServiceIdsPort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.collection.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadScheduledTriggerPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadServiceChangePort;
import dev.jazzybyte.onseoul.notification.port.out.LoadSubscriptionPort;
import dev.jazzybyte.onseoul.user.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.user.port.out.RefreshTokenStorePort;
import dev.jazzybyte.onseoul.notification.port.out.LoadBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveBatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveDispatchPort;
import dev.jazzybyte.onseoul.notification.port.out.SubscriptionFilterParserPort;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import dev.jazzybyte.onseoul.chat.port.out.DeleteChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatRoomPort;
import dev.jazzybyte.onseoul.collection.port.out.SaveCollectionHistoryPort;
import dev.jazzybyte.onseoul.collection.port.out.SavePublicServicePort;
import dev.jazzybyte.onseoul.collection.port.out.SaveServiceChangeLogPort;
import dev.jazzybyte.onseoul.notification.port.out.SaveSubscriptionPort;
import dev.jazzybyte.onseoul.user.port.out.SaveUserPort;
import dev.jazzybyte.onseoul.collection.port.out.SeoulDatasetFetchPort;
import dev.jazzybyte.onseoul.user.port.out.TokenIssuerPort;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.Mockito.doNothing;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

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
class SecurityConfigTest {

    @Autowired private MockMvc mockMvc;
    @Autowired private TokenIssuerPort tokenIssuerPort;

    // Mock all outbound ports
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
    @DisplayName("GET /actuator/health — 인증 없이 200을 반환한다")
    void actuatorHealth_noAuth_returns200() throws Exception {
        mockMvc.perform(get("/actuator/health"))
                .andExpect(status().isOk());
    }

    @Test
    @DisplayName("보호된 엔드포인트에 인증 없이 요청하면 401을 반환한다")
    void protectedEndpoint_noAuth_returns401() throws Exception {
        mockMvc.perform(get("/some-protected-path"))
                .andExpect(status().isUnauthorized())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"));
    }

    @Test
    @DisplayName("유효한 Bearer 토큰으로 보호된 엔드포인트에 요청하면 인증을 통과한다")
    void protectedEndpoint_withValidToken_passes401() throws Exception {
        String token = tokenIssuerPort.generateAccessToken(1L);
        doNothing().when(collectDatasetUseCase).collectAll();

        mockMvc.perform(post("/admin/collection/trigger")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk());
    }

    @Test
    @DisplayName("만료된 Bearer 토큰으로 보호된 엔드포인트에 요청하면 401을 반환한다")
    void protectedEndpoint_withExpiredToken_returns401() throws Exception {
        JwtTokenIssuer expiredIssuer = new JwtTokenIssuer(
                "dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=",
                -1L, -1L);
        String expiredToken = expiredIssuer.generateAccessToken(1L);

        mockMvc.perform(get("/some-protected-path")
                        .header("Authorization", "Bearer " + expiredToken))
                .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("POST /auth/logout — userId 속성이 없어도(null) 204를 반환한다")
    void logout_withoutUserId_returns204() throws Exception {
        mockMvc.perform(post("/auth/logout"))
                .andExpect(status().isNoContent());
    }

    @Test
    @DisplayName("변조된 Bearer 토큰으로 보호된 엔드포인트에 요청하면 401을 반환한다")
    void protectedEndpoint_withTamperedToken_returns401() throws Exception {
        String token = tokenIssuerPort.generateAccessToken(1L) + "tampered";

        mockMvc.perform(get("/some-protected-path")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("POST /admin/collection/trigger — 인증 없이 요청하면 401을 반환한다")
    void adminEndpoint_noAuth_returns401() throws Exception {
        mockMvc.perform(post("/admin/collection/trigger"))
                .andExpect(status().isUnauthorized());
    }

    // ── 쿠키 기반 인증 (JwtAuthenticationFilter 쿠키 폴백) ──────────

    @Test
    @DisplayName("access_token 쿠키만 있고 Authorization 헤더가 없어도 보호된 엔드포인트를 통과한다")
    void protectedEndpoint_withAccessTokenCookieOnly_passes() throws Exception {
        String token = tokenIssuerPort.generateAccessToken(1L);
        doNothing().when(collectDatasetUseCase).collectAll();

        mockMvc.perform(post("/admin/collection/trigger")
                        .cookie(new jakarta.servlet.http.Cookie("access_token", token)))
                .andExpect(status().isOk());
    }

    @Test
    @DisplayName("헤더와 쿠키 둘 다 있을 때 헤더(유효) 우선 — 쿠키는 무효 토큰이어도 인증 성공")
    void protectedEndpoint_headerTakesPriorityOverCookie() throws Exception {
        String validToken = tokenIssuerPort.generateAccessToken(1L);
        doNothing().when(collectDatasetUseCase).collectAll();

        mockMvc.perform(post("/admin/collection/trigger")
                        .header("Authorization", "Bearer " + validToken)
                        .cookie(new jakarta.servlet.http.Cookie("access_token", "invalid-cookie-token")))
                .andExpect(status().isOk());
    }

    @Test
    @DisplayName("만료된 access_token 쿠키로 보호된 엔드포인트에 요청하면 401을 반환한다")
    void protectedEndpoint_withExpiredCookie_returns401() throws Exception {
        JwtTokenIssuer expiredIssuer = new JwtTokenIssuer(
                "dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=",
                -1L, -1L);
        String expiredToken = expiredIssuer.generateAccessToken(1L);

        mockMvc.perform(get("/some-protected-path")
                        .cookie(new jakarta.servlet.http.Cookie("access_token", expiredToken)))
                .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("GET /auth/me — 토큰 없이 요청하면 Spring Security가 컨트롤러 진입 전 401을 반환한다")
    void me_withoutToken_returns401() throws Exception {
        mockMvc.perform(get("/auth/me"))
                .andExpect(status().isUnauthorized())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"));

        verifyNoInteractions(getMeUseCase);
    }
}
