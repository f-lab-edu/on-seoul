package dev.jazzybyte.onseoul.user.adapter.out.persistence;

import dev.jazzybyte.onseoul.user.domain.User;
import org.springframework.stereotype.Component;

@Component
class UserPersistenceMapper {

    User toDomain(UserJpaEntity entity) {
        return new User(
                entity.getId(),
                entity.getProvider(),
                entity.getProviderId(),
                entity.getEmail(),
                entity.getNickname(),
                entity.getPhoneNumber(),
                entity.getStatus(),
                entity.getCreatedAt(),
                entity.getUpdatedAt()
        );
    }

    UserJpaEntity toEntity(User user) {
        return new UserJpaEntity(
                user.getProvider(),
                user.getProviderId(),
                user.getEmail(),
                user.getNickname(),
                user.getStatus()
        );
    }

    UserJpaEntity updateEntity(UserJpaEntity entity, User user) {
        entity.updateProfile(user.getEmail(), user.getNickname());
        entity.updatePhoneNumber(user.getPhoneNumber());
        entity.updateStatus(user.getStatus());
        return entity;
    }
}
