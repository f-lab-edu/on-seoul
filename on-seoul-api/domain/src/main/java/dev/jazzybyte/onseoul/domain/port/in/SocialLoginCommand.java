package dev.jazzybyte.onseoul.domain.port.in;

public record SocialLoginCommand(String provider, String providerId, String email, String nickname) {}
