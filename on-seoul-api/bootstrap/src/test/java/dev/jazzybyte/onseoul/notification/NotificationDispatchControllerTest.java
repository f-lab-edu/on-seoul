package dev.jazzybyte.onseoul.notification;

import dev.jazzybyte.onseoul.notification.adapter.in.web.NotificationDispatchController;
import dev.jazzybyte.onseoul.notification.domain.DispatchStatus;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.port.in.ListDispatchesUseCase;
import dev.jazzybyte.onseoul.notification.port.in.ListDispatchesUseCase.DispatchPage;
import dev.jazzybyte.onseoul.shared.adapter.in.web.GlobalExceptionHandler;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.security.oauth2.client.servlet.OAuth2ClientWebSecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityFilterAutoConfiguration;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import java.time.Instant;
import java.util.List;

import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(controllers = {NotificationDispatchController.class, GlobalExceptionHandler.class},
        excludeAutoConfiguration = {
                SecurityAutoConfiguration.class,
                SecurityFilterAutoConfiguration.class,
                OAuth2ClientWebSecurityAutoConfiguration.class
        })
class NotificationDispatchControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private ListDispatchesUseCase listDispatchesUseCase;

    @Test
    @DisplayName("GET /api/notifications/dispatches — 인증된 사용자 → 200")
    void list_authenticated_returns200() throws Exception {
        NotificationDispatch d = new NotificationDispatch(
                1234L, 1L, 12L, DispatchStatus.SUCCESS,
                Instant.parse("2026-05-26T14:30:00Z"), "title", "body",
                null, null, 0, Instant.now(), Instant.now());
        when(listDispatchesUseCase.list(eq(1L), eq(null), eq(20)))
                .thenReturn(new DispatchPage(List.of(d), null));

        mockMvc.perform(get("/api/notifications/dispatches").requestAttr("userId", 1L))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.dispatches[0].id").value(1234))
                .andExpect(jsonPath("$.dispatches[0].subscriptionId").value(12))
                .andExpect(jsonPath("$.dispatches[0].title").value("title"))
                .andExpect(jsonPath("$.dispatches[0].status").value("SUCCESS"))
                .andExpect(jsonPath("$.nextCursor").isEmpty());
    }

    @Test
    @DisplayName("GET /api/notifications/dispatches — 인증 없음 → 401")
    void list_unauthenticated_returns401() throws Exception {
        mockMvc.perform(get("/api/notifications/dispatches"))
                .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("GET /api/notifications/dispatches?cursor=&size= — query 전달")
    void list_withCursorAndSize() throws Exception {
        when(listDispatchesUseCase.list(eq(1L), eq(500L), eq(50)))
                .thenReturn(new DispatchPage(List.of(), null));

        mockMvc.perform(get("/api/notifications/dispatches")
                        .requestAttr("userId", 1L)
                        .param("cursor", "500")
                        .param("size", "50"))
                .andExpect(status().isOk());
    }
}
