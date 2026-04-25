package dev.jazzybyte.onseoul.adapter.in.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.domain.port.out.TokenIssuerPort;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;

import java.nio.charset.StandardCharsets;
import java.util.Map;

@Configuration
@EnableWebSecurity
public class SecurityConfig {

    private final TokenIssuerPort tokenIssuerPort;
    private final OAuth2LoginSuccessHandler oauth2LoginSuccessHandler;
    private final ObjectMapper objectMapper;

    public SecurityConfig(final TokenIssuerPort tokenIssuerPort,
                          final OAuth2LoginSuccessHandler oauth2LoginSuccessHandler,
                          final ObjectMapper objectMapper) {
        this.tokenIssuerPort = tokenIssuerPort;
        this.oauth2LoginSuccessHandler = oauth2LoginSuccessHandler;
        this.objectMapper = objectMapper;
    }

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http) throws Exception {
        http
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(session ->
                        session.sessionCreationPolicy(SessionCreationPolicy.IF_REQUIRED))
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/actuator/health").permitAll()
                        .requestMatchers("/auth/**").permitAll()
                        .requestMatchers("/oauth2/authorization/**", "/login/oauth2/code/**").permitAll()
                        .anyRequest().authenticated()
                )
                .oauth2Login(oauth2 ->
                        oauth2.successHandler(oauth2LoginSuccessHandler)
                )
                .exceptionHandling(ex -> ex
                        .authenticationEntryPoint((request, response, authException) ->
                                writeErrorResponse(response, HttpServletResponse.SC_UNAUTHORIZED,
                                        "UNAUTHORIZED", "인증이 필요합니다."))
                        .accessDeniedHandler((request, response, accessDeniedException) ->
                                writeErrorResponse(response, HttpServletResponse.SC_FORBIDDEN,
                                        "FORBIDDEN", "접근 권한이 없습니다."))
                )
                .addFilterBefore(new JwtAuthenticationFilter(tokenIssuerPort),
                        UsernamePasswordAuthenticationFilter.class);

        return http.build();
    }

    private void writeErrorResponse(HttpServletResponse response, int status,
                                    String code, String message) {
        try {
            response.setStatus(status);
            response.setContentType(MediaType.APPLICATION_JSON_VALUE);
            response.setCharacterEncoding(StandardCharsets.UTF_8.name());
            objectMapper.writeValue(response.getWriter(), Map.of("code", code, "message", message));
        } catch (Exception ignored) {
        }
    }
}
