package dev.jazzybyte.onseoul.adapter.in.web;

import dev.jazzybyte.onseoul.adapter.in.security.OAuth2LoginSuccessHandler;
import dev.jazzybyte.onseoul.domain.port.in.GetMeUseCase;
import dev.jazzybyte.onseoul.domain.port.in.LogoutUseCase;
import dev.jazzybyte.onseoul.domain.port.in.MeResult;
import dev.jazzybyte.onseoul.domain.port.in.RefreshTokenUseCase;
import dev.jazzybyte.onseoul.domain.port.in.TokenResponse;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/auth")
@RequiredArgsConstructor
public class AuthController {

    private final RefreshTokenUseCase refreshTokenUseCase;
    private final LogoutUseCase logoutUseCase;
    private final OAuth2LoginSuccessHandler cookieHelper;
    private final GetMeUseCase getMeUseCase;

    @PostMapping("/token/refresh")
    public ResponseEntity<Void> refresh(
            @CookieValue(name = OAuth2LoginSuccessHandler.REFRESH_TOKEN_COOKIE, required = false)
            String refreshToken,
            HttpServletResponse response) {
        if (refreshToken == null) {
            return ResponseEntity.badRequest().build();
        }
        TokenResponse tokens = refreshTokenUseCase.refresh(refreshToken);
        response.addHeader(HttpHeaders.SET_COOKIE, cookieHelper.buildAccessCookie(tokens.accessToken()).toString());
        response.addHeader(HttpHeaders.SET_COOKIE, cookieHelper.buildRefreshCookie(tokens.refreshToken()).toString());
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/logout")
    public ResponseEntity<Void> logout(
            @RequestAttribute(required = false) Long userId,
            HttpServletResponse response) {
        if (userId != null) {
            logoutUseCase.logout(userId);
        }
        response.addHeader(HttpHeaders.SET_COOKIE, cookieHelper.expireAccessCookie().toString());
        response.addHeader(HttpHeaders.SET_COOKIE, cookieHelper.expireRefreshCookie().toString());
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/me")
    public ResponseEntity<MeResponse> me(@RequestAttribute(required = false) Long userId) {
        if (userId == null) {
            throw new OnSeoulApiException(ErrorCode.UNAUTHORIZED);
        }
        MeResult result = getMeUseCase.getMe(userId);
        return ResponseEntity.ok(new MeResponse(result.id(), result.nickname(), result.status()));
    }
}
