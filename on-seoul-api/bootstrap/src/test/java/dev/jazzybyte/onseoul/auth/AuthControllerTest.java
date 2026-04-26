package dev.jazzybyte.onseoul.auth;

import dev.jazzybyte.onseoul.adapter.in.web.AuthController;
import dev.jazzybyte.onseoul.adapter.in.web.GlobalExceptionHandler;
import dev.jazzybyte.onseoul.domain.port.in.LogoutUseCase;
import dev.jazzybyte.onseoul.domain.port.in.RefreshTokenUseCase;
import dev.jazzybyte.onseoul.domain.port.in.TokenResponse;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.security.servlet.SecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityFilterAutoConfiguration;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@WebMvcTest(controllers = {AuthController.class, GlobalExceptionHandler.class},
        excludeAutoConfiguration = {
                SecurityAutoConfiguration.class,
                SecurityFilterAutoConfiguration.class,
                org.springframework.boot.autoconfigure.security.oauth2.client.servlet.OAuth2ClientAutoConfiguration.class,
                org.springframework.boot.autoconfigure.security.oauth2.client.servlet.OAuth2ClientWebSecurityAutoConfiguration.class
        })
class AuthControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private RefreshTokenUseCase refreshTokenUseCase;

    @MockitoBean
    private LogoutUseCase logoutUseCase;

    @Test
    @DisplayName("POST /auth/token/refresh - 유효한 토큰으로 새 Access Token과 Refresh Token을 반환한다")
    void refresh_validToken_returnsNewTokenPair() throws Exception {
        when(refreshTokenUseCase.refresh(anyString()))
                .thenReturn(new TokenResponse("new-access-token", "new-refresh-token"));

        mockMvc.perform(post("/auth/token/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"valid-refresh-token\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.accessToken").value("new-access-token"))
                .andExpect(jsonPath("$.refreshToken").value("new-refresh-token"));
    }

    @Test
    @DisplayName("POST /auth/token/refresh - refreshToken 필드가 빈 문자열이면 400을 반환한다")
    void refresh_blankRefreshToken_returns400() throws Exception {
        mockMvc.perform(post("/auth/token/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"\"}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("POST /auth/token/refresh - refreshToken 필드가 없으면 400을 반환한다")
    void refresh_missingRefreshToken_returns400() throws Exception {
        mockMvc.perform(post("/auth/token/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("POST /auth/token/refresh - 유효하지 않은 토큰이면 401을 반환한다")
    void refresh_invalidToken_returns401() throws Exception {
        when(refreshTokenUseCase.refresh(anyString()))
                .thenThrow(new OnSeoulApiException(ErrorCode.INVALID_REFRESH_TOKEN));

        mockMvc.perform(post("/auth/token/refresh")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"refreshToken\":\"bad-token\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_REFRESH_TOKEN"));
    }

    @Test
    @DisplayName("POST /auth/logout - 인증된 사용자가 로그아웃하면 204를 반환한다")
    void logout_authenticatedUser_returns204() throws Exception {
        doNothing().when(logoutUseCase).logout(anyLong());

        mockMvc.perform(post("/auth/logout")
                        .requestAttr("userId", 42L))
                .andExpect(status().isNoContent());
    }
}
