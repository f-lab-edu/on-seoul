package dev.jazzybyte.onseoul.user;

import dev.jazzybyte.onseoul.crypto.CryptoException;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.shared.adapter.in.web.GlobalExceptionHandler;
import dev.jazzybyte.onseoul.user.adapter.in.web.UserController;
import dev.jazzybyte.onseoul.user.port.in.UpdateContactUseCase;
import dev.jazzybyte.onseoul.user.port.in.UpdateContactUseCase.UpdateContactCommand;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.security.oauth2.client.servlet.OAuth2ClientWebSecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityFilterAutoConfiguration;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.patch;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(controllers = {UserController.class, GlobalExceptionHandler.class},
        excludeAutoConfiguration = {
                SecurityAutoConfiguration.class,
                SecurityFilterAutoConfiguration.class,
                OAuth2ClientWebSecurityAutoConfiguration.class
        })
class UserControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private UpdateContactUseCase updateContactUseCase;

    // ── happy path ────────────────────────────────────────────────

    @Test
    @DisplayName("PATCH /api/users/me/contact — 인증된 사용자, 유효한 phoneNumber → 200")
    void updateContact_authenticatedUser_returns200() throws Exception {
        doNothing().when(updateContactUseCase).updateContact(eq(1L), any(UpdateContactCommand.class));

        mockMvc.perform(patch("/api/users/me/contact")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"phoneNumber\":\"010-1234-5678\"}"))
                .andExpect(status().isOk());

        verify(updateContactUseCase).updateContact(eq(1L),
                eq(new UpdateContactCommand("010-1234-5678")));
    }

    @Test
    @DisplayName("PATCH /api/users/me/contact — phoneNumber null 허용 → 200")
    void updateContact_nullPhoneNumber_returns200() throws Exception {
        doNothing().when(updateContactUseCase).updateContact(eq(2L), any(UpdateContactCommand.class));

        mockMvc.perform(patch("/api/users/me/contact")
                        .requestAttr("userId", 2L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"phoneNumber\":null}"))
                .andExpect(status().isOk());

        verify(updateContactUseCase).updateContact(eq(2L),
                eq(new UpdateContactCommand(null)));
    }

    // ── auth bypass ───────────────────────────────────────────────

    @Test
    @DisplayName("PATCH /api/users/me/contact — userId 없음(인증 미통과) → 401")
    void updateContact_noUserId_returns401() throws Exception {
        mockMvc.perform(patch("/api/users/me/contact")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"phoneNumber\":\"010-0000-0000\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"));

        verifyNoInteractions(updateContactUseCase);
    }

    // ── input validation ──────────────────────────────────────────

    @Test
    @DisplayName("PATCH /api/users/me/contact — phoneNumber 21자 초과 → 400")
    void updateContact_phoneNumberTooLong_returns400() throws Exception {
        mockMvc.perform(patch("/api/users/me/contact")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"phoneNumber\":\"010-1234-5678-extra!!\"}"))  // 21자
                .andExpect(status().isBadRequest());

        verifyNoInteractions(updateContactUseCase);
    }

    // ── downstream failure ────────────────────────────────────────

    @Test
    @DisplayName("PATCH /api/users/me/contact — CryptoException 발생 시 500 + internal_error 반환")
    void updateContact_cryptoException_returns500() throws Exception {
        doThrow(new CryptoException("decryption failed", new RuntimeException()))
                .when(updateContactUseCase).updateContact(eq(1L), any(UpdateContactCommand.class));

        mockMvc.perform(patch("/api/users/me/contact")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"phoneNumber\":\"010-1111-2222\"}"))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.error").value("internal_error"));

        verify(updateContactUseCase).updateContact(eq(1L), any(UpdateContactCommand.class));
    }

    @Test
    @DisplayName("PATCH /api/users/me/contact — 존재하지 않는 userId → 404")
    void updateContact_userNotFound_returns404() throws Exception {
        doThrow(new OnSeoulApiException(ErrorCode.USER_NOT_FOUND))
                .when(updateContactUseCase).updateContact(eq(99L), any(UpdateContactCommand.class));

        mockMvc.perform(patch("/api/users/me/contact")
                        .requestAttr("userId", 99L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"phoneNumber\":\"010-9999-9999\"}"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("USER_NOT_FOUND"));
    }
}
