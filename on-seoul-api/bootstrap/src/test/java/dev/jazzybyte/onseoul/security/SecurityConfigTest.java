package dev.jazzybyte.onseoul.security;

import dev.jazzybyte.onseoul.adapter.in.security.JwtTokenIssuer;
import dev.jazzybyte.onseoul.domain.port.in.CollectDatasetUseCase;
import dev.jazzybyte.onseoul.domain.port.in.LogoutUseCase;
import dev.jazzybyte.onseoul.domain.port.in.RefreshTokenUseCase;
import dev.jazzybyte.onseoul.domain.port.out.GeocodingPort;
import dev.jazzybyte.onseoul.domain.port.out.LoadApiSourceCatalogPort;
import dev.jazzybyte.onseoul.domain.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.domain.port.out.LoadPublicServicePort;
import dev.jazzybyte.onseoul.domain.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.domain.port.out.RefreshTokenStorePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveChatMessagePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveChatRoomPort;
import dev.jazzybyte.onseoul.domain.port.out.SaveCollectionHistoryPort;
import dev.jazzybyte.onseoul.domain.port.out.SavePublicServicePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveServiceChangeLogPort;
import dev.jazzybyte.onseoul.domain.port.out.SaveUserPort;
import dev.jazzybyte.onseoul.domain.port.out.SeoulDatasetFetchPort;
import dev.jazzybyte.onseoul.domain.port.out.TokenIssuerPort;
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
        "ai.service.stream-timeout-seconds=120"
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
    @MockitoBean SeoulDatasetFetchPort seoulDatasetFetchPort;
    @MockitoBean GeocodingPort geocodingPort;
    @MockitoBean StringRedisTemplate stringRedisTemplate;
    @MockitoBean CollectDatasetUseCase collectDatasetUseCase;
    @MockitoBean RefreshTokenUseCase refreshTokenUseCase;
    @MockitoBean LogoutUseCase logoutUseCase;
    @MockitoBean SaveChatRoomPort saveChatRoomPort;
    @MockitoBean LoadChatRoomPort loadChatRoomPort;
    @MockitoBean SaveChatMessagePort saveChatMessagePort;

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
}
