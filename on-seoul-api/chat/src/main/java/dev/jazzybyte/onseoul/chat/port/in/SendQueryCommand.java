package dev.jazzybyte.onseoul.chat.port.in;

public record SendQueryCommand(Long userId, Long roomId, String question, Double lat, Double lng) {}
