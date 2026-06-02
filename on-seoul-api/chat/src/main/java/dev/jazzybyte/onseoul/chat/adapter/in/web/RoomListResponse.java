package dev.jazzybyte.onseoul.chat.adapter.in.web;

import java.util.List;

public record RoomListResponse(
        List<RoomSummaryResponse> rooms,
        String nextCursor
) {}
