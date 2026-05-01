package dev.jazzybyte.onseoul.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.security.jwt.JwtAuthenticationFilter;
import dev.jazzybyte.onseoul.security.jwt.JwtProvider;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
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

@Slf4j
@Configuration
@EnableWebSecurity
public class SecurityConfig {

    private final JwtProvider jwtProvider;
    private final OAuth2LoginSuccessHandler oauth2LoginSuccessHandler;
    private final ObjectMapper objectMapper;

    public SecurityConfig(final JwtProvider jwtProvider,
                          final OAuth2LoginSuccessHandler oauth2LoginSuccessHandler,
                          final ObjectMapper objectMapper) {
        this.jwtProvider = jwtProvider;
        this.oauth2LoginSuccessHandler = oauth2LoginSuccessHandler;
        this.objectMapper = objectMapper;
    }

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http) throws Exception {
        http
                .csrf(AbstractHttpConfigurer::disable)
                .sessionManagement(session ->
                        // OAuth2 Code Flow는 redirect → callback 사이에 state를 세션에 저장해야 하므로
                        // IF_REQUIRED 사용. API 인증은 JwtAuthenticationFilter가 JWT로만 처리.
                        session.sessionCreationPolicy(SessionCreationPolicy.IF_REQUIRED))
                // 인증 없이 접근이 필요한 엔드포인트만 명시적으로 `permitAll()`로 등록
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/actuator/health").permitAll()
                        .requestMatchers("/auth/login", "/auth/token/refresh").permitAll()
                        .requestMatchers("/oauth2/authorization/**", "/login/oauth2/code/**").permitAll()
                        .anyRequest().authenticated()
                )
                .oauth2Login(oauth2 ->
                        oauth2.successHandler(oauth2LoginSuccessHandler))
                .exceptionHandling(ex -> ex
                        .authenticationEntryPoint((request, response, authException) ->
                                writeErrorResponse(response, HttpServletResponse.SC_UNAUTHORIZED,
                                        "UNAUTHORIZED", "인증이 필요합니다."))
                        .accessDeniedHandler((request, response, accessDeniedException) ->
                                writeErrorResponse(response, HttpServletResponse.SC_FORBIDDEN,
                                        "FORBIDDEN", "접근 권한이 없습니다."))
                )
                .addFilterBefore(new JwtAuthenticationFilter(jwtProvider),
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
        } catch (Exception e) {
            log.warn("에러 응답 작성 실패: {}", e.getMessage());
        }
    }
}
