package dev.jazzybyte.onseoul.notification.domain;

import lombok.Getter;

import java.time.Instant;

@Getter
public class NotificationSubscription {

    private Long id;
    private Long userId;
    private String serviceId;
    private String filter;
    private Instant lastNotifiedAt;
    private Instant createdAt;

    /** Reconstitute from persistence. */
    public NotificationSubscription(Long id, Long userId, String serviceId, String filter,
                                    Instant lastNotifiedAt, Instant createdAt) {
        this.id = id;
        this.userId = userId;
        this.serviceId = serviceId;
        this.filter = filter;
        this.lastNotifiedAt = lastNotifiedAt;
        this.createdAt = createdAt;
    }

    private NotificationSubscription() {}

    /** Factory method — creates a new subscription with an empty filter. */
    public static NotificationSubscription create(Long userId, String serviceId) {
        NotificationSubscription s = new NotificationSubscription();
        s.userId = userId;
        s.serviceId = serviceId;
        s.filter = "{}";
        s.createdAt = Instant.now();
        return s;
    }

    /** Updates lastNotifiedAt to the given instant. Called only on push success. */
    public void markNotified(Instant notifiedAt) {
        this.lastNotifiedAt = notifiedAt;
    }
}
