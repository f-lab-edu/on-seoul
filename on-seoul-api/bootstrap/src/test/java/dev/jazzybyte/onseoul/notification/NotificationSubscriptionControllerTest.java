package dev.jazzybyte.onseoul.notification;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.notification.adapter.in.web.NotificationSubscriptionController;
import dev.jazzybyte.onseoul.notification.application.SubscriptionView;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import dev.jazzybyte.onseoul.notification.port.in.CreateSubscriptionUseCase;
import dev.jazzybyte.onseoul.notification.port.in.CreateSubscriptionUseCase.CreateSubscriptionCommand;
import dev.jazzybyte.onseoul.notification.port.in.DeleteSubscriptionUseCase;
import dev.jazzybyte.onseoul.notification.port.in.ListSubscriptionsUseCase;
import dev.jazzybyte.onseoul.notification.port.in.UpdateSubscriptionUseCase;
import dev.jazzybyte.onseoul.shared.adapter.in.web.GlobalExceptionHandler;
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

import java.time.Instant;
import java.util.List;
import java.util.Set;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(controllers = {NotificationSubscriptionController.class, GlobalExceptionHandler.class},
        excludeAutoConfiguration = {
                SecurityAutoConfiguration.class,
                SecurityFilterAutoConfiguration.class,
                OAuth2ClientWebSecurityAutoConfiguration.class
        })
class NotificationSubscriptionControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private ListSubscriptionsUseCase listUseCase;
    @MockitoBean
    private CreateSubscriptionUseCase createUseCase;
    @MockitoBean
    private UpdateSubscriptionUseCase updateUseCase;
    @MockitoBean
    private DeleteSubscriptionUseCase deleteUseCase;

    private SubscriptionView sampleView(Long id, Long userId) {
        return new SubscriptionView(
                id, userId,
                new SubscriptionFilter(Set.of("RECEIVING"), Set.of(), Set.of(), Set.of()),
                Set.of(NotificationChannel.EMAIL),
                null, Instant.parse("2026-05-01T00:00:00Z"));
    }

    @Test
    @DisplayName("GET /api/notifications/subscriptions — 인증된 사용자 → 200")
    void list_authenticated_returns200() throws Exception {
        when(listUseCase.list(1L)).thenReturn(List.of(sampleView(10L, 1L)));

        mockMvc.perform(get("/api/notifications/subscriptions").requestAttr("userId", 1L))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.subscriptions[0].id").value(10))
                .andExpect(jsonPath("$.subscriptions[0].filter.statuses[0]").value("RECEIVING"))
                .andExpect(jsonPath("$.subscriptions[0].channels[0]").value("EMAIL"));
    }

    @Test
    @DisplayName("GET /api/notifications/subscriptions — userId 없음 → 401")
    void list_noUserId_returns401() throws Exception {
        mockMvc.perform(get("/api/notifications/subscriptions"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("UNAUTHORIZED"));
    }

    @Test
    @DisplayName("POST /api/notifications/subscriptions — happy path → 201")
    void create_valid_returns201() throws Exception {
        when(createUseCase.create(eq(1L), any(CreateSubscriptionCommand.class)))
                .thenReturn(sampleView(11L, 1L));

        mockMvc.perform(post("/api/notifications/subscriptions")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"filter":{"statuses":["RECEIVING"]},"channels":["EMAIL"]}
                                """))
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.id").value(11));
    }

    @Test
    @DisplayName("POST /api/notifications/subscriptions — 빈 필터(전체 구독) → 400")
    void create_emptyFilter_returns400() throws Exception {
        mockMvc.perform(post("/api/notifications/subscriptions")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"channels\":[\"EMAIL\"]}"))
                .andExpect(status().isBadRequest());
        verifyNoInteractions(createUseCase);
    }

    @Test
    @DisplayName("POST /api/notifications/subscriptions — 키워드 4개 초과 → 400")
    void create_tooManyKeywords_returns400() throws Exception {
        mockMvc.perform(post("/api/notifications/subscriptions")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"filter\":{\"keywords\":[\"a\",\"b\",\"c\",\"d\"]},\"channels\":[\"EMAIL\"]}"))
                .andExpect(status().isBadRequest());
        verifyNoInteractions(createUseCase);
    }

    @Test
    @DisplayName("POST /api/notifications/subscriptions — 빈 channels → 400")
    void create_emptyChannels_returns400() throws Exception {
        mockMvc.perform(post("/api/notifications/subscriptions")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"filter\":{\"statuses\":[\"RECEIVING\"]},\"channels\":[]}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("POST /api/notifications/subscriptions — 중복 → 409")
    void create_duplicate_returns409() throws Exception {
        when(createUseCase.create(eq(1L), any()))
                .thenThrow(new OnSeoulApiException(ErrorCode.SUBSCRIPTION_CONFLICT));

        mockMvc.perform(post("/api/notifications/subscriptions")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"filter\":{\"statuses\":[\"RECEIVING\"]},\"channels\":[\"EMAIL\"]}"))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("SUBSCRIPTION_CONFLICT"));
    }

    @Test
    @DisplayName("PATCH /api/notifications/subscriptions/{id} — 다른 유저 구독 → 403")
    void update_otherUser_returns403() throws Exception {
        when(updateUseCase.update(eq(1L), eq(99L), any()))
                .thenThrow(new OnSeoulApiException(ErrorCode.FORBIDDEN));

        mockMvc.perform(patch("/api/notifications/subscriptions/99")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"channels\":[\"EMAIL\",\"SMS\"]}"))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("FORBIDDEN"));
    }

    @Test
    @DisplayName("PATCH /api/notifications/subscriptions/{id} — happy path → 200")
    void update_valid_returns200() throws Exception {
        when(updateUseCase.update(eq(1L), eq(10L), any())).thenReturn(sampleView(10L, 1L));

        mockMvc.perform(patch("/api/notifications/subscriptions/10")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"channels\":[\"EMAIL\",\"SMS\"]}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(10));
    }

    @Test
    @DisplayName("PATCH — filter/channels 둘 다 null → 400 (application 검증)")
    void update_emptyBody_returns400() throws Exception {
        when(updateUseCase.update(eq(1L), eq(10L), any()))
                .thenThrow(new OnSeoulApiException(ErrorCode.INVALID_INPUT,
                        "filter 또는 channels 중 하나는 반드시 제공되어야 합니다."));

        mockMvc.perform(patch("/api/notifications/subscriptions/10")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("DELETE /api/notifications/subscriptions/{id} — 본인 구독 → 204")
    void delete_own_returns204() throws Exception {
        doNothing().when(deleteUseCase).delete(1L, 10L);

        mockMvc.perform(delete("/api/notifications/subscriptions/10")
                        .requestAttr("userId", 1L))
                .andExpect(status().isNoContent());
        verify(deleteUseCase).delete(1L, 10L);
    }

    @Test
    @DisplayName("PATCH — channels=[] (빈 배열) → 400 (application 검증)")
    void update_emptyChannels_returns400() throws Exception {
        when(updateUseCase.update(eq(1L), eq(10L), any()))
                .thenThrow(new OnSeoulApiException(ErrorCode.INVALID_INPUT,
                        "채널은 최소 1개 이상이어야 합니다."));

        mockMvc.perform(patch("/api/notifications/subscriptions/10")
                        .requestAttr("userId", 1L)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"channels\":[]}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("DELETE — 다른 유저 구독 → 403")
    void delete_otherUser_returns403() throws Exception {
        doThrow(new OnSeoulApiException(ErrorCode.FORBIDDEN))
                .when(deleteUseCase).delete(1L, 99L);

        mockMvc.perform(delete("/api/notifications/subscriptions/99")
                        .requestAttr("userId", 1L))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.code").value("FORBIDDEN"));
    }

    @Test
    @DisplayName("DELETE — userId 없음 → 401")
    void delete_noUserId_returns401() throws Exception {
        mockMvc.perform(delete("/api/notifications/subscriptions/10"))
                .andExpect(status().isUnauthorized());
        verifyNoInteractions(deleteUseCase);
    }

    @Test
    @DisplayName("DELETE — 미존재 → 404")
    void delete_notFound_returns404() throws Exception {
        doThrow(new OnSeoulApiException(ErrorCode.SUBSCRIPTION_NOT_FOUND))
                .when(deleteUseCase).delete(1L, 99L);

        mockMvc.perform(delete("/api/notifications/subscriptions/99")
                        .requestAttr("userId", 1L))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("SUBSCRIPTION_NOT_FOUND"));
    }
}
