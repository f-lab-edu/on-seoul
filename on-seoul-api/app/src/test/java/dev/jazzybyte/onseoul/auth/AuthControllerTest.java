package dev.jazzybyte.onseoul.auth;

import dev.jazzybyte.onseoul.auth.dto.TokenResponse;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.security.OAuth2LoginSuccessHandler;
import dev.jazzybyte.onseoul.security.jwt.JwtProvider;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.security.oauth2.client.servlet.OAuth2ClientWebSecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityFilterAutoConfiguration;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.http.HttpHeaders;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import jakarta.servlet.http.Cookie;

import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@WebMvcTest(controllers = AuthController.class,
        excludeAutoConfiguration = {
                SecurityAutoConfiguration.class,
                SecurityFilterAutoConfiguration.class,
                OAuth2ClientWebSecurityAutoConfiguration.class
        })
class AuthControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private AuthService authService;

    @MockitoBean
    private JwtProvider jwtProvider;

    @MockitoBean
    private OAuth2LoginSuccessHandler oAuth2LoginSuccessHandler;

    // ── refresh ───────────────────────────────────────────────────

    @Test
    @DisplayName("POST /auth/token/refresh - 유효한 쿠키로 새 토큰 쿠키를 발급하고 204를 반환한다")
    void refresh_validCookie_setsNewCookiesAndReturns204() throws Exception {
        when(authService.refresh("valid-refresh-token"))
                .thenReturn(new TokenResponse("new-access", "new-refresh"));
        when(oAuth2LoginSuccessHandler.buildAccessCookie("new-access"))
                .thenReturn(org.springframework.http.ResponseCookie
                        .from("access_token", "new-access").path("/").build());
        when(oAuth2LoginSuccessHandler.buildRefreshCookie("new-refresh"))
                .thenReturn(org.springframework.http.ResponseCookie
                        .from("refresh_token", "new-refresh").path("/auth").build());

        mockMvc.perform(post("/auth/token/refresh")
                        .cookie(new Cookie("refresh_token", "valid-refresh-token")))
                .andExpect(status().isNoContent())
                .andExpect(header().exists(HttpHeaders.SET_COOKIE));
    }

    @Test
    @DisplayName("POST /auth/token/refresh - refresh_token 쿠키가 없으면 401을 반환한다")
    void refresh_missingCookie_returns401() throws Exception {
        mockMvc.perform(post("/auth/token/refresh"))
                .andExpect(status().isUnauthorized());

        verifyNoInteractions(authService);
    }

    @Test
    @DisplayName("POST /auth/token/refresh - 유효하지 않은 토큰 쿠키면 401을 반환한다")
    void refresh_invalidCookie_returns401() throws Exception {
        when(authService.refresh("bad-token"))
                .thenThrow(new OnSeoulApiException(ErrorCode.INVALID_REFRESH_TOKEN));

        mockMvc.perform(post("/auth/token/refresh")
                        .cookie(new Cookie("refresh_token", "bad-token")))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_REFRESH_TOKEN"));
    }

    // ── logout ────────────────────────────────────────────────────

    @Test
    @DisplayName("POST /auth/logout - 인증된 사용자가 로그아웃하면 쿠키를 만료시키고 204를 반환한다")
    void logout_authenticatedUser_expiresCookiesAndReturns204() throws Exception {
        when(oAuth2LoginSuccessHandler.expireAccessCookie())
                .thenReturn(org.springframework.http.ResponseCookie
                        .from("access_token", "").maxAge(0).path("/").build());
        when(oAuth2LoginSuccessHandler.expireRefreshCookie())
                .thenReturn(org.springframework.http.ResponseCookie
                        .from("refresh_token", "").maxAge(0).path("/auth").build());
        doNothing().when(authService).logout(anyLong());

        mockMvc.perform(post("/auth/logout")
                        .requestAttr("userId", 42L))
                .andExpect(status().isNoContent())
                .andExpect(header().exists(HttpHeaders.SET_COOKIE));

        verify(authService).logout(42L);
    }

    @Test
    @DisplayName("POST /auth/logout - userId가 없어도 쿠키를 만료시키고 204를 반환한다 (토큰 만료 상태)")
    void logout_withoutUserId_expiresCookiesAndReturns204() throws Exception {
        when(oAuth2LoginSuccessHandler.expireAccessCookie())
                .thenReturn(org.springframework.http.ResponseCookie
                        .from("access_token", "").maxAge(0).path("/").build());
        when(oAuth2LoginSuccessHandler.expireRefreshCookie())
                .thenReturn(org.springframework.http.ResponseCookie
                        .from("refresh_token", "").maxAge(0).path("/auth").build());

        mockMvc.perform(post("/auth/logout"))
                .andExpect(status().isNoContent());

        verify(authService, never()).logout(anyLong());
    }
}
