package dev.jazzybyte.onseoul.notification.adapter.in.web;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.notification.port.in.ListDispatchesUseCase;
import dev.jazzybyte.onseoul.notification.port.in.ListDispatchesUseCase.DispatchPage;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/notifications/dispatches")
@RequiredArgsConstructor
public class NotificationDispatchController {

    private final ListDispatchesUseCase listDispatchesUseCase;

    @GetMapping
    public ResponseEntity<DispatchListResponse> list(
            @RequestAttribute(required = false) Long userId,
            @RequestParam(required = false) Long cursor,
            @RequestParam(required = false, defaultValue = "20") int size) {
        if (userId == null) {
            throw new OnSeoulApiException(ErrorCode.UNAUTHORIZED);
        }
        DispatchPage page = listDispatchesUseCase.list(userId, cursor, size);
        List<DispatchResponse> dispatches = page.dispatches().stream()
                .map(DispatchResponse::from)
                .toList();
        return ResponseEntity.ok(new DispatchListResponse(dispatches, page.nextCursor()));
    }

    public record DispatchListResponse(List<DispatchResponse> dispatches, Long nextCursor) {}
}
