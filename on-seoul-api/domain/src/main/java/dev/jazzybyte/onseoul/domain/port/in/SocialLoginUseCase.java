package dev.jazzybyte.onseoul.domain.port.in;

public interface SocialLoginUseCase {
    TokenResponse socialLogin(SocialLoginCommand command);
}
