package dev.jazzybyte.onseoul.auth.dto;

import jakarta.validation.constraints.NotBlank;

public record RefreshRequest(@NotBlank String refreshToken) {
}
