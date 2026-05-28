package dev.jazzybyte.onseoul.notification.adapter.in.web;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.notification.application.SubscriptionView;
import dev.jazzybyte.onseoul.notification.port.in.CreateSubscriptionUseCase;
import dev.jazzybyte.onseoul.notification.port.in.DeleteSubscriptionUseCase;
import dev.jazzybyte.onseoul.notification.port.in.ListSubscriptionsUseCase;
import dev.jazzybyte.onseoul.notification.port.in.UpdateSubscriptionUseCase;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/notifications/subscriptions")
@RequiredArgsConstructor
public class NotificationSubscriptionController {

    private final ListSubscriptionsUseCase listSubscriptionsUseCase;
    private final CreateSubscriptionUseCase createSubscriptionUseCase;
    private final UpdateSubscriptionUseCase updateSubscriptionUseCase;
    private final DeleteSubscriptionUseCase deleteSubscriptionUseCase;

    @GetMapping
    public ResponseEntity<ListResponse> list(
            @RequestAttribute(required = false) Long userId) {
        requireUserId(userId);
        List<SubscriptionView> views = listSubscriptionsUseCase.list(userId);
        List<SubscriptionResponse> body = views.stream()
                .map(SubscriptionResponse::from)
                .toList();
        return ResponseEntity.ok(new ListResponse(body));
    }

    @PostMapping
    public ResponseEntity<SubscriptionResponse> create(
            @RequestAttribute(required = false) Long userId,
            @Valid @RequestBody CreateSubscriptionRequest request) {
        requireUserId(userId);
        SubscriptionView created = createSubscriptionUseCase.create(userId, request.toCommand());
        return ResponseEntity.status(201).body(SubscriptionResponse.from(created));
    }

    @PatchMapping("/{id}")
    public ResponseEntity<SubscriptionResponse> update(
            @RequestAttribute(required = false) Long userId,
            @PathVariable Long id,
            @Valid @RequestBody UpdateSubscriptionRequest request) {
        requireUserId(userId);
        SubscriptionView updated = updateSubscriptionUseCase.update(userId, id, request.toCommand());
        return ResponseEntity.ok(SubscriptionResponse.from(updated));
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Void> delete(
            @RequestAttribute(required = false) Long userId,
            @PathVariable Long id) {
        requireUserId(userId);
        deleteSubscriptionUseCase.delete(userId, id);
        return ResponseEntity.noContent().build();
    }

    private void requireUserId(Long userId) {
        if (userId == null) {
            throw new OnSeoulApiException(ErrorCode.UNAUTHORIZED);
        }
    }

    public record ListResponse(List<SubscriptionResponse> subscriptions) {}
}
