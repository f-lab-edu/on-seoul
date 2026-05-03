package dev.jazzybyte.onseoul.domain.port.in;

public interface RefreshTokenUseCase {
    TokenResponse refresh(String refreshToken);
}
