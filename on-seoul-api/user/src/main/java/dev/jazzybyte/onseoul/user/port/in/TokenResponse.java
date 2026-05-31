package dev.jazzybyte.onseoul.user.port.in;

public record TokenResponse(Long userId, String accessToken, String refreshToken) {}
