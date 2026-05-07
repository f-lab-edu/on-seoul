package dev.jazzybyte.onseoul.domain.port.in;

import dev.jazzybyte.onseoul.domain.model.UserStatus;

public record MeResult(Long id, String nickname, UserStatus status) {}
