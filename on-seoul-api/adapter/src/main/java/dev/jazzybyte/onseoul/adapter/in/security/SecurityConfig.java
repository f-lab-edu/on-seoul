package dev.jazzybyte.onseoul.adapter.in.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.domain.port.out.TokenIssuerPort;
import jakarta.servlet.DispatcherType;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.security.web.authentication.session.NullAuthenticatedSessionStrategy;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;
import java.util.Map;

@Slf4j
@Configuration
@EnableWebSecurity
public class SecurityConfig {

    private final TokenIssuerPort tokenIssuerPort;
    private final OAuth2LoginSuccessHandler oauth2LoginSuccessHandler;
    private final CookieOAuth2AuthorizationRequestRepository authorizationRequestRepository;
    private final ObjectMapper objectMapper;
    private final String frontendBaseUrl;

    @Value("${app.cors.allowed-origins}")
    private String corsAllowedOriginsRaw;

    public SecurityConfig(final TokenIssuerPort tokenIssuerPort,
                          final OAuth2LoginSuccessHandler oauth2LoginSuccessHandler,
                          final CookieOAuth2AuthorizationRequestRepository authorizationRequestRepository,
                          final ObjectMapper objectMapper,
                          @Value("${app.frontend-base-url}") final String frontendBaseUrl) {
        this.tokenIssuerPort = tokenIssuerPort;
        this.oauth2LoginSuccessHandler = oauth2LoginSuccessHandler;
        this.authorizationRequestRepository = authorizationRequestRepository;
        this.objectMapper = objectMapper;
        this.frontendBaseUrl = frontendBaseUrl;
    }

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http) throws Exception {
        http
                .cors(cors -> cors.configurationSource(corsConfigurationSource()))  // 추가
                .csrf(AbstractHttpConfigurer::disable)
                // STATELESS: OAuth2 state를 CookieOAuth2AuthorizationRequestRepository가
                // 쿠키로 관리하므로 서버 세션 불필요. 분산 환경에서도 state 검증이 정상 동작한다.
                // NullAuthenticatedSessionStrategy: OAuth2 인증 성공 후
                // AbstractAuthenticationProcessingFilter가 SessionAuthenticationStrategy를
                // 실행하면서 세션을 생성하는 것을 차단한다.
                .sessionManagement(session -> session
                        .sessionCreationPolicy(SessionCreationPolicy.STATELESS)
                        .sessionAuthenticationStrategy(new NullAuthenticatedSessionStrategy()))
                // 인증 없이 접근이 필요한 엔드포인트만 명시적으로 `permitAll()`로 등록
                .authorizeHttpRequests(auth -> auth
                        // ERROR: SSE 스트리밍 중 예외 발생 시 Tomcat이 /error로 재디스패치하는데,
                        // 이때 SecurityContext가 비어 있어 AnonymousUser로 인가 필터를 다시 통과한다.
                        // /error를 permitAll()로 등록해도 DispatcherType이 ERROR이면 JwtAuthenticationFilter가
                        // 재실행되어 401이 발생하므로 타입 레벨에서 차단한다.
                        // ASYNC: DeferredResult/WebAsyncTask 등 비동기 요청에서 SecurityContext가
                        // 전파되지 않아 불필요하게 인가가 막히는 경우를 방지한다.
                        .dispatcherTypeMatchers(DispatcherType.ERROR, DispatcherType.ASYNC).permitAll()
                        .requestMatchers("/actuator/health", "/error").permitAll()
                        .requestMatchers("/auth/token/refresh", "/auth/logout").permitAll()
                        .requestMatchers("/oauth2/authorization/**", "/login/oauth2/code/**").permitAll()
                        .anyRequest().authenticated()
                )
                .oauth2Login(oauth2 -> oauth2
                        .authorizationEndpoint(endpoint -> endpoint
                                .authorizationRequestRepository(authorizationRequestRepository))
                        .successHandler(oauth2LoginSuccessHandler)
                        // OAuth2 인증 실패 시 Spring Security 기본값(/login?error)은 API 서버에서
                        // 401을 반환하므로 프론트엔드 콜백 URL로 직접 리다이렉트한다.
                        .failureHandler((request, response, exception) -> {
                            log.warn("[Security] OAuth2 인증 실패 - {}: {}", exception.getClass().getSimpleName(), exception.getMessage());
                            response.sendRedirect(frontendBaseUrl + "/oauth/callback?error=auth_failed");
                        }))
                .exceptionHandling(ex -> ex
                        .authenticationEntryPoint((request,
                                                   response,
                                                   authException) -> {
                            log.warn("[Security] 인증 실패 - URI: {}, 사유: {}", request.getRequestURI(), authException.getMessage());
                            writeErrorResponse(response, HttpServletResponse.SC_UNAUTHORIZED,
                                    "UNAUTHORIZED", "인증이 필요합니다.");
                        })
                        .accessDeniedHandler((request,
                                              response,
                                              accessDeniedException) -> {
                            log.warn("[Security] 인가 실패 - URI: {}, 사유: {}", request.getRequestURI(), accessDeniedException.getMessage());
                            writeErrorResponse(response, HttpServletResponse.SC_FORBIDDEN,
                                    "FORBIDDEN", "접근 권한이 없습니다.");

                        })
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
        } catch (Exception e) {
            log.warn("[Security] 에러 응답 작성 실패: {}", e.getMessage());
        }
    }


    @Bean
    public CorsConfigurationSource corsConfigurationSource() {
        List<String> allowedOrigins = Arrays.stream(corsAllowedOriginsRaw.split(","))
                .map(String::trim)
                .filter(s -> !s.isEmpty())
                .toList();

        CorsConfiguration config = new CorsConfiguration();
        config.setAllowedOriginPatterns(allowedOrigins);
        config.setAllowedMethods(List.of("GET", "POST", "PUT", "DELETE", "OPTIONS"));
        config.setAllowedHeaders(List.of("*"));
        config.setAllowCredentials(true); // 쿠키(access_token, refresh_token) 전송 필요
        config.setMaxAge(600L); // preflight 캐시 10분

        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", config);
        return source;
    }
}
