package dev.jazzybyte.onseoul.adapter.in.web;

import dev.jazzybyte.onseoul.domain.port.in.LogoutUseCase;
import dev.jazzybyte.onseoul.domain.port.in.RefreshTokenUseCase;
import dev.jazzybyte.onseoul.domain.port.in.TokenResponse;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/auth")
public class AuthController {

    private final RefreshTokenUseCase refreshTokenUseCase;
    private final LogoutUseCase logoutUseCase;

    public AuthController(final RefreshTokenUseCase refreshTokenUseCase,
                          final LogoutUseCase logoutUseCase) {
        this.refreshTokenUseCase = refreshTokenUseCase;
        this.logoutUseCase = logoutUseCase;
    }

    @PostMapping("/token/refresh")
    public ResponseEntity<TokenResponse> refresh(@Valid @RequestBody RefreshRequest request) {
        TokenResponse tokenResponse = refreshTokenUseCase.refresh(request.refreshToken());
        return ResponseEntity.ok(tokenResponse);
    }

    @PostMapping("/logout")
    public ResponseEntity<Void> logout(@RequestAttribute(required = false) Long userId) {
        if (userId != null) {
            logoutUseCase.logout(userId);
        }
        return ResponseEntity.noContent().build();
    }
}
