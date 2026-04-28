package dev.jazzybyte.onseoul.adapter.in.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.jazzybyte.onseoul.domain.port.in.SocialLoginCommand;
import dev.jazzybyte.onseoul.domain.port.in.SocialLoginUseCase;
import dev.jazzybyte.onseoul.domain.port.in.TokenResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.MediaType;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.client.authentication.OAuth2AuthenticationToken;
import org.springframework.security.oauth2.core.user.OAuth2User;
import org.springframework.security.web.authentication.AuthenticationSuccessHandler;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Map;

@Component
public class OAuth2LoginSuccessHandler implements AuthenticationSuccessHandler {

    /** HttpOnly 쿠키로 전달되는 Access Token 쿠키 이름. JwtAuthenticationFilter와 공유. */
    public static final String ACCESS_TOKEN_COOKIE  = "access_token";
    /** HttpOnly 쿠키로 전달되는 Refresh Token 쿠키 이름. AuthController와 공유. */
    public static final String REFRESH_TOKEN_COOKIE = "refresh_token";

    private final SocialLoginUseCase socialLoginUseCase;
    private final ObjectMapper objectMapper;

    public OAuth2LoginSuccessHandler(final SocialLoginUseCase socialLoginUseCase,
                                     final ObjectMapper objectMapper) {
        this.socialLoginUseCase = socialLoginUseCase;
        this.objectMapper = objectMapper;
    }

    @Override
    public void onAuthenticationSuccess(HttpServletRequest request,
                                        HttpServletResponse response,
                                        Authentication authentication) throws IOException {
        OAuth2AuthenticationToken oauthToken = (OAuth2AuthenticationToken) authentication;
        OAuth2User oauth2User = oauthToken.getPrincipal();
        String provider = oauthToken.getAuthorizedClientRegistrationId();

        Object idAttr = oauth2User.getAttribute("sub") != null
                ? oauth2User.getAttribute("sub")
                : oauth2User.getAttribute("id");
        String providerId = idAttr != null ? idAttr.toString() : null;

        String email;
        String nickname;
        if ("kakao".equals(provider)) {
            Map<String, Object> kakaoAccount = oauth2User.getAttribute("kakao_account");
            Map<String, Object> properties = oauth2User.getAttribute("properties");
            email    = kakaoAccount != null ? (String) kakaoAccount.get("email") : null;
            nickname = properties   != null ? (String) properties.get("nickname") : null;
        } else {
            email    = oauth2User.getAttribute("email");
            nickname = oauth2User.getAttribute("name");
        }

        try {
            SocialLoginCommand command = new SocialLoginCommand(provider, providerId, email, nickname);
            TokenResponse tokenResponse = socialLoginUseCase.socialLogin(command);

            response.setContentType(MediaType.APPLICATION_JSON_VALUE);
            response.setCharacterEncoding(StandardCharsets.UTF_8.name());
            response.setStatus(HttpServletResponse.SC_OK);
            objectMapper.writeValue(response.getWriter(), tokenResponse);

        } catch (dev.jazzybyte.onseoul.exception.OnSeoulApiException ex) {
            response.setStatus(HttpServletResponse.SC_FORBIDDEN);
            response.setContentType(MediaType.APPLICATION_JSON_VALUE);
            response.setCharacterEncoding(StandardCharsets.UTF_8.name());
            objectMapper.writeValue(response.getWriter(),
                    Map.of("code", "FORBIDDEN", "message", ex.getMessage()));
        }
    }
}
