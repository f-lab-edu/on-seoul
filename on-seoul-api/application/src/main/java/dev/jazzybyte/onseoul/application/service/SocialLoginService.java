package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.model.User;
import dev.jazzybyte.onseoul.domain.model.UserStatus;
import dev.jazzybyte.onseoul.domain.port.in.SocialLoginCommand;
import dev.jazzybyte.onseoul.domain.port.in.SocialLoginUseCase;
import dev.jazzybyte.onseoul.domain.port.in.TokenResponse;
import dev.jazzybyte.onseoul.domain.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.domain.port.out.RefreshTokenStorePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveUserPort;
import dev.jazzybyte.onseoul.domain.port.out.TokenIssuerPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Slf4j
@Service
@RequiredArgsConstructor
public class SocialLoginService implements SocialLoginUseCase {

    private final LoadUserPort loadUserPort;
    private final SaveUserPort saveUserPort;
    private final TokenIssuerPort tokenIssuerPort;
    private final RefreshTokenStorePort refreshTokenStorePort;

    @Override
    @Transactional
    public TokenResponse socialLogin(SocialLoginCommand command) {
        User user = loadUserPort.findByProviderAndProviderId(command.provider(), command.providerId())
                .map(existing -> {
                    existing.updateProfile(command.email(), command.nickname());
                    return saveUserPort.save(existing);
                })
                .orElseGet(() -> saveUserPort.save(User.create(command)));

        if (user.getStatus() != UserStatus.ACTIVE) {
            throw new OnSeoulApiException(ErrorCode.FORBIDDEN, "비활성화된 계정입니다.");
        }

        log.info("[Security] 소셜 로그인 - userId={}, provider={}, providerId={}",
                user.getId(), user.getProvider(), user.getProviderId());
        String accessToken = tokenIssuerPort.generateAccessToken(user.getId());
        String refreshToken = tokenIssuerPort.generateRefreshToken(user.getId());
        refreshTokenStorePort.save(user.getId(), refreshToken, tokenIssuerPort.getRefreshTokenMinutes());
        return new TokenResponse(accessToken, refreshToken);
    }
}
