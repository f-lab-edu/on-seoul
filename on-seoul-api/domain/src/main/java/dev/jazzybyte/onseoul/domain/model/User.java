package dev.jazzybyte.onseoul.domain.model;

import dev.jazzybyte.onseoul.domain.port.in.SocialLoginCommand;
import lombok.Getter;

import java.time.OffsetDateTime;

@Getter
public class User {

    private Long id;
    private String provider;
    private String providerId;
    private String email;
    private String nickname;
    private UserStatus status;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;

    /** Reconstitute from persistence. */
    public User(Long id, String provider, String providerId, String email, String nickname,
                UserStatus status, OffsetDateTime createdAt, OffsetDateTime updatedAt) {
        this.id = id;
        this.provider = provider;
        this.providerId = providerId;
        this.email = email;
        this.nickname = nickname;
        this.status = status;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    /** Factory method — creates a new ACTIVE user from OAuth2 attributes. */
    public static User create(SocialLoginCommand command) {
        User user = new User();
        user.provider = command.provider();
        user.providerId = command.providerId();
        user.email = command.email();
        user.nickname = command.nickname();
        user.status = UserStatus.ACTIVE;
        user.createdAt = OffsetDateTime.now();
        user.updatedAt = OffsetDateTime.now();
        return user;
    }

    private User() {}

    public void updateProfile(String email, String nickname) {
        this.email = email;
        this.nickname = nickname;
        this.updatedAt = OffsetDateTime.now();
    }
}
