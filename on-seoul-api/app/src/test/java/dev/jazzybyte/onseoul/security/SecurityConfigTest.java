package dev.jazzybyte.onseoul.security;

import dev.jazzybyte.onseoul.auth.AuthService;
import dev.jazzybyte.onseoul.collector.service.CollectionService;
import dev.jazzybyte.onseoul.collector.service.GeocodingService;
import dev.jazzybyte.onseoul.collector.service.UpsertService;
import dev.jazzybyte.onseoul.repository.UserRepository;
import dev.jazzybyte.onseoul.security.jwt.JwtProvider;
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
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

/**
 * SecurityConfig 통합 테스트.
 *
 * <p>실제 FilterChain을 통해 인증 규칙을 검증한다.
 * DB/Redis는 Mock으로 대체하여 외부 인프라 없이 실행한다.</p>
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
        "kakao.api.key=test"
})
class SecurityConfigTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JwtProvider jwtProvider;

    // Mocked infrastructure beans required for context load
    @MockitoBean
    CollectionService collectionService;
    @MockitoBean
    UpsertService upsertService;
    @MockitoBean
    GeocodingService geocodingService;
    @MockitoBean
    UserRepository userRepository;
    @MockitoBean
    StringRedisTemplate stringRedisTemplate;
    @MockitoBean
    AuthService authService;

    @Test
    @DisplayName("GET /actuator/health — 인증 없이 200을 반환한다")
    void actuatorHealth_noAuth_returns200() throws Exception {
        mockMvc.perform(get("/actuator/health"))
                .andExpect(status().isOk());
    }

    @Test
    @DisplayName("보호된 엔드포인트에 인증 없이 요청하면 401을 반환한다")
    void protectedEndpoint_noAuth_returns401() throws Exception {
        // /api/** is not in the permit list — any unknown authenticated path triggers 401
        mockMvc.perform(get("/some-protected-path"))
                .andExpect(status().isUnauthorized())
                .andExpect(content().contentTypeCompatibleWith(MediaType.APPLICATION_JSON))
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"));
    }

    @Test
    @DisplayName("유효한 Bearer 토큰으로 보호된 엔드포인트에 요청하면 인증을 통과한다")
    void protectedEndpoint_withValidToken_passes401() throws Exception {
        String token = jwtProvider.generateAccessToken(1L);

        // POST /auth/logout is under /auth/** which is permitAll,
        // but we use a known-authenticated path: /admin/collection/trigger
        // CollectionService is mocked so it won't throw
        doNothing().when(collectionService).collectAll();

        mockMvc.perform(post("/admin/collection/trigger")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isOk());
    }

    @Test
    @DisplayName("만료된 Bearer 토큰으로 보호된 엔드포인트에 요청하면 401을 반환한다")
    void protectedEndpoint_withExpiredToken_returns401() throws Exception {
        // TTL = -1 minutes → immediately expired
        JwtProvider expiredProvider = new JwtProvider(
                "dGVzdC1zZWNyZXQta2V5LWZvci1qdW5pdC10ZXN0cy10aGlzLWlzLTI1Ni1iaXQ=",
                -1L, -1L);
        String expiredToken = expiredProvider.generateAccessToken(1L);

        mockMvc.perform(get("/some-protected-path")
                        .header("Authorization", "Bearer " + expiredToken))
                .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("POST /auth/logout — userId 속성이 없어도(null) 204를 반환한다")
    void logout_withoutUserId_returns204() throws Exception {
        // No JWT header → filter does not set userId attribute → controller receives null
        mockMvc.perform(post("/auth/logout"))
                .andExpect(status().isNoContent());
    }

    @Test
    @DisplayName("변조된 Bearer 토큰으로 보호된 엔드포인트에 요청하면 401을 반환한다")
    void protectedEndpoint_withTamperedToken_returns401() throws Exception {
        String token = jwtProvider.generateAccessToken(1L) + "tampered";

        mockMvc.perform(get("/some-protected-path")
                        .header("Authorization", "Bearer " + token))
                .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("POST /admin/collection/trigger — 인증 없이 요청하면 401을 반환한다 (M-1)")
    void adminEndpoint_noAuth_returns401() throws Exception {
        mockMvc.perform(post("/admin/collection/trigger"))
                .andExpect(status().isUnauthorized());
    }
}
