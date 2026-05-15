package dev.jazzybyte.onseoul.chat.adapter.in.web;

import jakarta.validation.constraints.NotBlank;

public record QueryRequest(
        Long roomId,
        @NotBlank String question,
        Double lat,
        Double lng
) {}
