package dev.jazzybyte.onseoul.user.application;

import dev.jazzybyte.onseoul.user.domain.User;
import dev.jazzybyte.onseoul.user.domain.UserStatus;
import dev.jazzybyte.onseoul.user.port.in.SocialLoginCommand;
import dev.jazzybyte.onseoul.user.port.in.SocialLoginUseCase;
import dev.jazzybyte.onseoul.user.port.in.TokenResponse;
import dev.jazzybyte.onseoul.user.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.user.port.out.RefreshTokenStorePort;
import dev.jazzybyte.onseoul.user.port.out.SaveUserPort;
import dev.jazzybyte.onseoul.user.port.out.TokenIssuerPort;
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
                .orElseGet(() -> registerNewUser(command));

        if (user.getStatus() != UserStatus.ACTIVE) {
            throw new OnSeoulApiException(ErrorCode.FORBIDDEN, "비활성화된 계정입니다.");
        }

        log.info("[Security] 소셜 로그인 - userId={}, provider={}, providerId={}",
                user.getId(), user.getProvider(), user.getProviderId());
        String accessToken = tokenIssuerPort.generateAccessToken(user.getId());
        String refreshToken = tokenIssuerPort.generateRefreshToken(user.getId());
        refreshTokenStorePort.save(user.getId(), refreshToken, tokenIssuerPort.getRefreshTokenMinutes());
        return new TokenResponse(user.getId(), accessToken, refreshToken);
    }

    /**
     * 신규 가입 처리. 정책 B(기존 벤더 안내):
     * 같은 이메일이 다른 벤더로 이미 가입돼 있으면 신규 INSERT 대신 안내 예외를 던진다.
     * 카카오 이메일이 null일 수 있는데, email_hash가 null이면 유니크 제약과 충돌하지 않으므로 선검사를 건너뛴다.
     */
    private User registerNewUser(SocialLoginCommand command) {
        if (command.email() != null) {
            loadUserPort.findByEmail(command.email()).ifPresent(existing -> {
                if (!existing.getProvider().equals(command.provider())) {
                    throw new OnSeoulApiException(ErrorCode.EMAIL_ALREADY_REGISTERED,
                            "이미 " + existing.getProvider() + "(으)로 가입된 이메일입니다.");
                }
            });
        }
        try {
            return saveUserPort.save(User.create(command));
        } catch (org.springframework.dao.DataIntegrityViolationException ex) {
            // 경쟁 조건 백스톱: 선검사 통과 후 동시 최초 로그인으로 email_hash 유니크 위반(23505) 발생.
            // 기존 벤더 정보를 알 수 없으므로 메시지는 일반화한다.
            log.warn("[Security] 소셜 로그인 신규 가입 중 이메일 중복(경쟁 조건) - provider={}, providerId={}",
                    command.provider(), command.providerId());
            throw new OnSeoulApiException(ErrorCode.EMAIL_ALREADY_REGISTERED,
                    ErrorCode.EMAIL_ALREADY_REGISTERED.getDefaultMessage(), ex);
        }
    }
}
