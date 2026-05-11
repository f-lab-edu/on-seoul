package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.port.in.LogoutUseCase;
import dev.jazzybyte.onseoul.domain.port.out.RefreshTokenStorePort;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@RequiredArgsConstructor
public class LogoutService implements LogoutUseCase {

    private final RefreshTokenStorePort refreshTokenStorePort;

    @Override
    public void logout(Long userId) {
        log.info("[Security] 로그아웃 - userId={}", userId);
        refreshTokenStorePort.delete(userId);
    }
}
