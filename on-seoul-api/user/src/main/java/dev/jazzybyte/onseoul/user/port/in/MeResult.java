package dev.jazzybyte.onseoul.user.port.in;

import dev.jazzybyte.onseoul.user.domain.UserStatus;

public record MeResult(Long id, String nickname, UserStatus status) {}
