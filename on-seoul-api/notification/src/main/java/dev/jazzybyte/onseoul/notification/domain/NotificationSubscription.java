package dev.jazzybyte.onseoul.notification.domain;

import lombok.Getter;

import java.time.Instant;
import java.util.Set;

@Getter
public class NotificationSubscription {

    private Long id;
    private Long userId;
    /**
     * JSONB string representation of {@link SubscriptionFilter}.
     * Populated by adapters on load. {@code null} for instances produced by
     * {@link #create(Long, SubscriptionFilter, Set)} until they are persisted —
     * adapters consult {@link #parsedFilter} and serialize via the persistence mapper.
     */
    private String filter;
    /**
     * In-memory representation of the filter for newly created (not-yet-persisted) instances.
     * The persistence adapter serializes this through the persistence mapper at INSERT time so
     * the application layer never touches JSON.
     */
    private SubscriptionFilter parsedFilter;
    private Set<NotificationChannel> channels;
    private Instant lastNotifiedAt;
    private Instant createdAt;

    private NotificationSubscription() {}

    private NotificationSubscription(Long id, Long userId, String filter,
                                     Set<NotificationChannel> channels,
                                     Instant lastNotifiedAt, Instant createdAt) {
        this.id = id;
        this.userId = userId;
        this.filter = filter;
        this.channels = channels;
        this.lastNotifiedAt = lastNotifiedAt;
        this.createdAt = createdAt;
    }

    /**
     * Reconstitute from persistence — invoked by JPA mapper only.
     * The reconstitute constructor is private so application/web layers cannot bypass the
     * domain factories.
     */
    public static NotificationSubscription ofPersistence(Long id, Long userId, String filter,
                                                         Set<NotificationChannel> channels,
                                                         Instant lastNotifiedAt, Instant createdAt) {
        return new NotificationSubscription(id, userId, filter, channels, lastNotifiedAt, createdAt);
    }

    /**
     * Factory — creates a new subscription with the given filter and channels.
     *
     * <p>{@link #parsedFilter} 는 어댑터가 INSERT 시 JSON 직렬화에 사용한다.
     * {@link #filter} (JSON 문자열) 은 빈 필터일 때 한해 표현이 명확한 {@code "{}"} 로 미리 채워둔다 —
     * 그 외 경우는 어댑터가 mapper 로 직렬화한 결과로 round-trip 후 도메인에 다시 채워진다.
     */
    public static NotificationSubscription create(Long userId,
                                                  SubscriptionFilter filter,
                                                  Set<NotificationChannel> channels) {
        NotificationSubscription s = new NotificationSubscription();
        s.userId = userId;
        SubscriptionFilter f = filter != null ? filter : SubscriptionFilter.empty();
        s.parsedFilter = f;
        if (f.isEmpty()) {
            s.filter = "{}";
        }
        s.channels = channels;
        s.createdAt = Instant.now();
        return s;
    }

    /** Convenience overload — creates a new subscription with an empty filter. */
    public static NotificationSubscription create(Long userId,
                                                  Set<NotificationChannel> channels) {
        return create(userId, SubscriptionFilter.empty(), channels);
    }

    /** Updates lastNotifiedAt to the given instant. Called only on push success. */
    public void markNotified(Instant notifiedAt) {
        this.lastNotifiedAt = notifiedAt;
    }
}
