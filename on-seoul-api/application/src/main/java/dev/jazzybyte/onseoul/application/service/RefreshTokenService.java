package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.model.User;
import dev.jazzybyte.onseoul.domain.model.UserStatus;
import dev.jazzybyte.onseoul.domain.port.in.RefreshTokenUseCase;
import dev.jazzybyte.onseoul.domain.port.in.TokenResponse;
import dev.jazzybyte.onseoul.domain.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.domain.port.out.RefreshTokenStorePort;
import dev.jazzybyte.onseoul.domain.port.out.TokenIssuerPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class RefreshTokenService implements RefreshTokenUseCase {

    private final TokenIssuerPort tokenIssuerPort;
    private final RefreshTokenStorePort refreshTokenStorePort;
    private final LoadUserPort loadUserPort;


    @Override
    public TokenResponse refresh(String refreshToken) {
        tokenIssuerPort.validateToken(refreshToken);
        Long userId = tokenIssuerPort.extractUserId(refreshToken);

        // 원자적 GET+DELETE: TOCTOU 경합 방지. 동시 요청 중 하나만 값을 가져온다.
        String stored = refreshTokenStorePort.getAndDelete(userId)
                .orElseThrow(() -> new OnSeoulApiException(
                        ErrorCode.INVALID_REFRESH_TOKEN, "유효하지 않은 리프레시 토큰입니다."));

        if (!stored.equals(refreshToken)) {
            throw new OnSeoulApiException(ErrorCode.INVALID_REFRESH_TOKEN, "유효하지 않은 리프레시 토큰입니다.");
        }

        User user = loadUserPort.findById(userId)
                .orElseThrow(() -> new OnSeoulApiException(ErrorCode.FORBIDDEN, "사용자를 찾을 수 없습니다."));
        if (user.getStatus() != UserStatus.ACTIVE) {
            throw new OnSeoulApiException(ErrorCode.FORBIDDEN, "비활성화된 계정입니다.");
        }

        String newAccessToken = tokenIssuerPort.generateAccessToken(userId);
        String newRefreshToken = tokenIssuerPort.generateRefreshToken(userId);
        refreshTokenStorePort.save(userId, newRefreshToken, tokenIssuerPort.getRefreshTokenMinutes());

        return new TokenResponse(newAccessToken, newRefreshToken);
    }
}
