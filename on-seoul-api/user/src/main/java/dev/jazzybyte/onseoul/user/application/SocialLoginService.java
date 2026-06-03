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
import dev.jazzybyte.onseoul.exception.EmailConflictException;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Slf4j
@Service
@RequiredArgsConstructor
public class SocialLoginService implements SocialLoginUseCase {

    private final LoadUserPort loadUserPort;
    private final SaveUserPort saveUserPort;
    private final NewUserRegistrar newUserRegistrar;
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
     *
     * <p><b>트랜잭션 격리</b>: 실제 INSERT는 {@link NewUserRegistrar#register}(REQUIRES_NEW)가 수행한다.
     * 경쟁 조건으로 유니크 위반이 나면 내부 트랜잭션만 롤백되고 {@link DataIntegrityViolationException}이
     * 여기로 전파되며, 그 시점은 이미 내부 트랜잭션 경계 밖이라 바깥 트랜잭션이 rollback-only로 오염되지 않는다.
     * 변환된 {@link EmailConflictException}이 바깥 {@code @Transactional}을 빠져나가면 커밋이 아니라 롤백으로 처리돼
     * UnexpectedRollbackException 없이 핸들러까지 도달한다.</p>
     */
    private User registerNewUser(SocialLoginCommand command) {
        if (command.email() != null) {
            loadUserPort.findByEmail(command.email()).ifPresent(existing -> {
                // 정책: "이메일 1개 = 계정 1개". findByProviderAndProviderId로 이미 1차 필터됐으므로
                // 여기 도달했다면 (a) 다른 벤더이거나 (b) 같은 벤더 + 다른 providerId 인 경우다.
                // 두 경우 모두 같은 이메일이면 충돌로 막는다(자동 계정 연동은 미검증 이메일 탈취 위험으로 미채택).
                // command.provider()는 OAuth2LoginSuccessHandler에서 registrationId로 항상 non-null이므로
                // command 쪽을 좌변에 두어 NPE를 피한다.
                throw new EmailConflictException(existing.getProvider(),
                        command.provider().equals(existing.getProvider())
                                ? "이미 가입된 이메일입니다."
                                : "이미 " + existing.getProvider() + "(으)로 가입된 이메일입니다.");
            });
        }
        try {
            return newUserRegistrar.register(command);
        } catch (DataIntegrityViolationException ex) {
            // 경쟁 조건 백스톱: 선검사 통과 후 동시 최초 로그인으로 email_hash 유니크 위반(23505) 발생.
            // 내부 REQUIRES_NEW 트랜잭션은 이미 롤백됐고, 여기서 변환만 한다.
            // 기존 벤더 정보를 알 수 없으므로 existingProvider=null로 둔다(핸들러가 provider 파라미터 생략).
            log.warn("[Security] 소셜 로그인 신규 가입 중 이메일 중복(경쟁 조건) - provider={}, providerId={}",
                    command.provider(), command.providerId());
            throw new EmailConflictException(null,
                    ErrorCode.EMAIL_ALREADY_REGISTERED.getDefaultMessage(), ex);
        }
    }
}
