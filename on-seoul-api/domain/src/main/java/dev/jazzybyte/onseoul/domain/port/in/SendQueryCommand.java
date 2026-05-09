package dev.jazzybyte.onseoul.domain.port.in;

public record SendQueryCommand(Long userId, Long roomId, String question, Double lat, Double lng) {}
