package dev.jazzybyte.onseoul.user.port.in;

public interface RefreshTokenUseCase {
    TokenResponse refresh(String refreshToken);
}
