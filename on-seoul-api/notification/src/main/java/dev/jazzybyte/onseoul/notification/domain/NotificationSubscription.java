package dev.jazzybyte.onseoul.notification.domain;

import lombok.Getter;

import java.time.Instant;
import java.util.Set;

@Getter
public class NotificationSubscription {

    private Long id;
    private Long userId;
    private String serviceId;
    private String filter;
    private Set<NotificationChannel> channels;
    private Instant lastNotifiedAt;
    private Instant createdAt;

    /** Reconstitute from persistence. */
    public NotificationSubscription(Long id, Long userId, String serviceId, String filter,
                                    Set<NotificationChannel> channels,
                                    Instant lastNotifiedAt, Instant createdAt) {
        this.id = id;
        this.userId = userId;
        this.serviceId = serviceId;
        this.filter = filter;
        this.channels = channels;
        this.lastNotifiedAt = lastNotifiedAt;
        this.createdAt = createdAt;
    }

    private NotificationSubscription() {}

    /** Factory method — creates a new subscription with an empty filter and given channels. */
    public static NotificationSubscription create(Long userId, String serviceId,
                                                  Set<NotificationChannel> channels) {
        NotificationSubscription s = new NotificationSubscription();
        s.userId = userId;
        s.serviceId = serviceId;
        s.filter = "{}";
        s.channels = channels;
        s.createdAt = Instant.now();
        return s;
    }

    /** Updates lastNotifiedAt to the given instant. Called only on push success. */
    public void markNotified(Instant notifiedAt) {
        this.lastNotifiedAt = notifiedAt;
    }
}
