package dev.jazzybyte.onseoul.controller;

import dev.jazzybyte.onseoul.adapter.in.web.CollectionController;
import dev.jazzybyte.onseoul.domain.port.in.CollectDatasetUseCase;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.security.oauth2.client.servlet.OAuth2ClientWebSecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityAutoConfiguration;
import org.springframework.boot.autoconfigure.security.servlet.SecurityFilterAutoConfiguration;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(controllers = CollectionController.class,
        excludeAutoConfiguration = {
                SecurityAutoConfiguration.class,
                SecurityFilterAutoConfiguration.class,
                OAuth2ClientWebSecurityAutoConfiguration.class
        })
class CollectionControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockitoBean
    private CollectDatasetUseCase collectDatasetUseCase;

    @Test
    @DisplayName("POST /admin/collection/trigger 호출 시 collectAll()이 실행되고 200을 반환한다")
    void trigger_calls_collect_all() throws Exception {
        mockMvc.perform(post("/admin/collection/trigger"))
                .andExpect(status().isOk());

        verify(collectDatasetUseCase).collectAll();
    }
}
