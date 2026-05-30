package dev.jazzybyte.onseoul.user.adapter.in.web;

import dev.jazzybyte.onseoul.user.domain.UserStatus;

public record MeResponse(Long id, String nickname, String phoneNumber, UserStatus status) {}
