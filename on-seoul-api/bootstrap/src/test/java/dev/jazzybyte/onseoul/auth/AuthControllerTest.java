package dev.jazzybyte.onseoul.auth;

import dev.jazzybyte.onseoul.adapter.in.security.OAuth2LoginSuccessHandler;
import dev.jazzybyte.onseoul.adapter.in.web.AuthController;
import dev.jazzybyte.onseoul.adapter.in.web.GlobalExceptionHandler;
import dev.jazzybyte.onseoul.domain.port.in.LogoutUseCase;
import dev.jazzybyte.onseoul.domain.port.in.RefreshTokenUseCase;
import dev.jazzybyte.onseoul.domain.port.in.TokenResponse;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import jakarta.servlet.http.Cookie;
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

import static org.hamcrest.Matchers.containsString;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@WebMvcTest(controllers = {AuthController.class, GlobalExceptionHandler.class},
        excludeAutoConfiguration = {
                SecurityAutoConfiguration.class,
                SecurityFilterAutoConfiguration.class,
                OAuth2ClientWebSecurityAutoConfiguration.class
        })
class AuthControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private RefreshTokenUseCase refreshTokenUseCase;

    @MockitoBean
    private LogoutUseCase logoutUseCase;

    @MockitoBean
    private OAuth2LoginSuccessHandler cookieHelper;

    // ── refresh ───────────────────────────────────────────────────

    @Test
    @DisplayName("POST /auth/token/refresh - 유효한 refresh_token 쿠키로 새 토큰을 Set-Cookie로 반환한다")
    void refresh_validCookie_returnsNewTokensAsSetCookie() throws Exception {
        when(refreshTokenUseCase.refresh("valid-refresh-token"))
                .thenReturn(new TokenResponse("new-access", "new-refresh"));
        when(cookieHelper.buildAccessCookie("new-access"))
                .thenReturn(org.springframework.http.ResponseCookie
                        .from("access_token", "new-access").path("/").maxAge(900).build());
        when(cookieHelper.buildRefreshCookie("new-refresh"))
                .thenReturn(org.springframework.http.ResponseCookie
                        .from("refresh_token", "new-refresh").path("/auth").maxAge(604800).build());

        mockMvc.perform(post("/auth/token/refresh")
                        .cookie(new Cookie("refresh_token", "valid-refresh-token")))
                .andExpect(status().isNoContent())
                .andExpect(header().exists(HttpHeaders.SET_COOKIE));
    }

    @Test
    @DisplayName("POST /auth/token/refresh - refresh_token 쿠키 없으면 400을 반환한다")
    void refresh_missingCookie_returns400() throws Exception {
        mockMvc.perform(post("/auth/token/refresh"))
                .andExpect(status().isBadRequest());

        verifyNoInteractions(refreshTokenUseCase);
    }

    @Test
    @DisplayName("POST /auth/token/refresh - 유효하지 않은 토큰이면 401을 반환한다")
    void refresh_invalidToken_returns401() throws Exception {
        when(refreshTokenUseCase.refresh(anyString()))
                .thenThrow(new OnSeoulApiException(ErrorCode.INVALID_REFRESH_TOKEN));

        mockMvc.perform(post("/auth/token/refresh")
                        .cookie(new Cookie("refresh_token", "bad-token")))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_REFRESH_TOKEN"));
    }

    // ── logout ────────────────────────────────────────────────────

    @Test
    @DisplayName("POST /auth/logout - 인증된 사용자가 로그아웃하면 쿠키 만료 + 204를 반환한다")
    void logout_authenticatedUser_expiresCookiesAndReturns204() throws Exception {
        doNothing().when(logoutUseCase).logout(anyLong());
        when(cookieHelper.expireAccessCookie())
                .thenReturn(org.springframework.http.ResponseCookie
                        .from("access_token", "").maxAge(0).path("/").build());
        when(cookieHelper.expireRefreshCookie())
                .thenReturn(org.springframework.http.ResponseCookie
                        .from("refresh_token", "").maxAge(0).path("/auth").build());

        mockMvc.perform(post("/auth/logout")
                        .requestAttr("userId", 42L))
                .andExpect(status().isNoContent())
                .andExpect(header().exists(HttpHeaders.SET_COOKIE))
                .andExpect(header().string(HttpHeaders.SET_COOKIE, containsString("Max-Age=0")));

        verify(logoutUseCase).logout(42L);
    }

    @Test
    @DisplayName("POST /auth/logout - userId 없어도 쿠키 만료 + 204를 반환한다 (토큰 만료 상태)")
    void logout_withoutUserId_expiresCookiesAndReturns204() throws Exception {
        when(cookieHelper.expireAccessCookie())
                .thenReturn(org.springframework.http.ResponseCookie
                        .from("access_token", "").maxAge(0).path("/").build());
        when(cookieHelper.expireRefreshCookie())
                .thenReturn(org.springframework.http.ResponseCookie
                        .from("refresh_token", "").maxAge(0).path("/auth").build());

        mockMvc.perform(post("/auth/logout"))
                .andExpect(status().isNoContent())
                .andExpect(header().exists(HttpHeaders.SET_COOKIE));

        verify(logoutUseCase, never()).logout(anyLong());
    }
}
