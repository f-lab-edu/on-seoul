package dev.jazzybyte.onseoul.adapter.in.web;

import jakarta.validation.constraints.NotBlank;

public record RefreshRequest(@NotBlank String refreshToken) {}
