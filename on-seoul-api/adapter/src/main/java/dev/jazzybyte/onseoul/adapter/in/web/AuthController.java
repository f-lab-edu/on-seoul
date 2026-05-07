package dev.jazzybyte.onseoul.adapter.in.web;

import dev.jazzybyte.onseoul.adapter.in.security.OAuth2LoginSuccessHandler;
import dev.jazzybyte.onseoul.domain.model.User;
import dev.jazzybyte.onseoul.domain.model.UserStatus;
import dev.jazzybyte.onseoul.domain.port.in.LogoutUseCase;
import dev.jazzybyte.onseoul.domain.port.in.RefreshTokenUseCase;
import dev.jazzybyte.onseoul.domain.port.in.TokenResponse;
import dev.jazzybyte.onseoul.domain.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/auth")
public class AuthController {

    private final RefreshTokenUseCase refreshTokenUseCase;
    private final LogoutUseCase logoutUseCase;
    private final OAuth2LoginSuccessHandler cookieHelper;
    private final LoadUserPort loadUserPort;

    public AuthController(final RefreshTokenUseCase refreshTokenUseCase,
                          final LogoutUseCase logoutUseCase,
                          final OAuth2LoginSuccessHandler cookieHelper,
                          final LoadUserPort loadUserPort) {
        this.refreshTokenUseCase = refreshTokenUseCase;
        this.logoutUseCase = logoutUseCase;
        this.cookieHelper = cookieHelper;
        this.loadUserPort = loadUserPort;
    }

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
        User user = loadUserPort.findById(userId)
                .orElseThrow(() -> new OnSeoulApiException(ErrorCode.USER_NOT_FOUND));
        if (user.getStatus() == UserStatus.SUSPENDED || user.getStatus() == UserStatus.DELETED) {
            throw new OnSeoulApiException(ErrorCode.FORBIDDEN);
        }
        return ResponseEntity.ok(new MeResponse(user.getId(), user.getNickname(), user.getStatus().name()));
    }
}
