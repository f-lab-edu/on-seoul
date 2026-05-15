package dev.jazzybyte.onseoul.user.port.in;

public interface SocialLoginUseCase {
    TokenResponse socialLogin(SocialLoginCommand command);
}
