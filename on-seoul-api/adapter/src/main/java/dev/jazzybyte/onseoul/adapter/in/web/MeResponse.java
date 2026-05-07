package dev.jazzybyte.onseoul.adapter.in.web;

import dev.jazzybyte.onseoul.domain.model.UserStatus;

public record MeResponse(Long id, String nickname, UserStatus status) {}
