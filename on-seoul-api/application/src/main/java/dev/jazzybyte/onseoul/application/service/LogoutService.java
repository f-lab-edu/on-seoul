package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.port.in.LogoutUseCase;
import dev.jazzybyte.onseoul.domain.port.out.RefreshTokenStorePort;
import org.springframework.stereotype.Service;

@Service
public class LogoutService implements LogoutUseCase {

    private final RefreshTokenStorePort refreshTokenStorePort;

    public LogoutService(final RefreshTokenStorePort refreshTokenStorePort) {
        this.refreshTokenStorePort = refreshTokenStorePort;
    }

    @Override
    public void logout(Long userId) {
        refreshTokenStorePort.delete(userId);
    }
}
