package dev.jazzybyte.onseoul.adapter.in.web;

import jakarta.validation.constraints.NotBlank;

public record QueryRequest(
        Long roomId,
        @NotBlank String question
) {}
