package dev.jazzybyte.onseoul.auth;

import dev.jazzybyte.onseoul.auth.dto.TokenResponse;
import dev.jazzybyte.onseoul.security.OAuth2LoginSuccessHandler;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/auth")
public class AuthController {

    private final AuthService authService;
    private final OAuth2LoginSuccessHandler oAuth2LoginSuccessHandler;

    public AuthController(final AuthService authService,
                          final OAuth2LoginSuccessHandler oAuth2LoginSuccessHandler) {
        this.authService = authService;
        this.oAuth2LoginSuccessHandler = oAuth2LoginSuccessHandler;
    }

    /**
     * Refresh Token 쿠키로 새 Access/Refresh Token 쌍을 발급한다(Token Rotation).
     *
     * <p>브라우저 클라이언트는 {@code refresh_token} HttpOnly 쿠키를 자동으로 전송한다.
     * 성공 시 두 토큰 모두 새 쿠키로 교체되며 응답 바디는 없다.</p>
     */
    @PostMapping("/token/refresh")
    public ResponseEntity<Void> refresh(
            @CookieValue(name = OAuth2LoginSuccessHandler.REFRESH_TOKEN_COOKIE, required = false)
            String refreshToken,
            HttpServletResponse response) {

        if (refreshToken == null) {
            return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
        }

        TokenResponse tokens = authService.refresh(refreshToken);
        response.addHeader(HttpHeaders.SET_COOKIE,
                oAuth2LoginSuccessHandler.buildAccessCookie(tokens.accessToken()).toString());
        response.addHeader(HttpHeaders.SET_COOKIE,
                oAuth2LoginSuccessHandler.buildRefreshCookie(tokens.refreshToken()).toString());
        return ResponseEntity.noContent().build();
    }

    /**
     * 현재 인증된 사용자를 로그아웃 처리한다.
     *
     * <p>JWT 필터가 {@code request} attribute {@code "userId"}에 Long을 주입한다.
     * Redis의 Refresh Token을 삭제하고 두 토큰 쿠키를 만료시킨다.</p>
     */
    @PostMapping("/logout")
    public ResponseEntity<Void> logout(
            @RequestAttribute(required = false) Long userId,
            HttpServletResponse response) {

        if (userId != null) {
            authService.logout(userId);
        }
        response.addHeader(HttpHeaders.SET_COOKIE,
                oAuth2LoginSuccessHandler.expireAccessCookie().toString());
        response.addHeader(HttpHeaders.SET_COOKIE,
                oAuth2LoginSuccessHandler.expireRefreshCookie().toString());
        return ResponseEntity.noContent().build();
    }
}
