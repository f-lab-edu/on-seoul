package dev.jazzybyte.onseoul.user.port.in;

public record SocialLoginCommand(String provider, String providerId, String email, String nickname) {}
