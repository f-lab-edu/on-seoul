package dev.jazzybyte.onseoul.adapter.out.persistence.user;

import dev.jazzybyte.onseoul.domain.model.User;
import dev.jazzybyte.onseoul.domain.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.domain.port.out.SaveUserPort;
import org.springframework.stereotype.Component;

import java.util.Optional;

@Component
class UserPersistenceAdapter implements LoadUserPort, SaveUserPort {

    private final UserJpaRepository jpaRepository;
    private final UserPersistenceMapper mapper;

    UserPersistenceAdapter(final UserJpaRepository jpaRepository,
                           final UserPersistenceMapper mapper) {
        this.jpaRepository = jpaRepository;
        this.mapper = mapper;
    }

    @Override
    public Optional<User> findByProviderAndProviderId(String provider, String providerId) {
        return jpaRepository.findByProviderAndProviderId(provider, providerId)
                .map(mapper::toDomain);
    }

    @Override
    public Optional<User> findById(Long id) {
        return jpaRepository.findById(id).map(mapper::toDomain);
    }

    @Override
    public User save(User user) {
        UserJpaEntity entity;
        if (user.getId() != null) {
            entity = jpaRepository.findById(user.getId())
                    .map(e -> mapper.updateEntity(e, user))
                    .orElseGet(() -> mapper.toEntity(user));
        } else {
            entity = mapper.toEntity(user);
        }
        return mapper.toDomain(jpaRepository.save(entity));
    }
}
