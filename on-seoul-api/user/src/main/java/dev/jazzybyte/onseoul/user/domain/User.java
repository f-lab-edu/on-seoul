package dev.jazzybyte.onseoul.user.domain;

import dev.jazzybyte.onseoul.user.port.in.SocialLoginCommand;
import lombok.Getter;

import java.time.OffsetDateTime;

@Getter
public class User {

    private Long id;
    private String provider;
    private String providerId;
    private String email;
    private String nickname;
    private String phoneNumber;
    private UserStatus status;
    private OffsetDateTime createdAt;
    private OffsetDateTime updatedAt;

    /** Reconstitute from persistence. */
    public User(Long id, String provider, String providerId, String email, String nickname,
                String phoneNumber, UserStatus status,
                OffsetDateTime createdAt, OffsetDateTime updatedAt) {
        this.id = id;
        this.provider = provider;
        this.providerId = providerId;
        this.email = email;
        this.nickname = nickname;
        this.phoneNumber = phoneNumber;
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
        user.phoneNumber = null;
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

    /** Updates phoneNumber. Email is managed by OAuth2 and is not changed here. */
    public void updateContact(String phoneNumber) {
        this.phoneNumber = phoneNumber;
        this.updatedAt = OffsetDateTime.now();
    }
}
