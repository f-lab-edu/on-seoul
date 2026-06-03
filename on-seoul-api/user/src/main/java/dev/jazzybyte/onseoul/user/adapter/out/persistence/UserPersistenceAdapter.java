package dev.jazzybyte.onseoul.user.adapter.out.persistence;

import dev.jazzybyte.onseoul.crypto.AesGcmEncryptor;
import dev.jazzybyte.onseoul.crypto.BlindIndexer;
import dev.jazzybyte.onseoul.user.domain.User;
import dev.jazzybyte.onseoul.user.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.user.port.out.SaveUserPort;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.Optional;

@Component
@Transactional
class UserPersistenceAdapter implements LoadUserPort, SaveUserPort {

    private final UserJpaRepository jpaRepository;
    private final UserPersistenceMapper mapper;
    private final AesGcmEncryptor encryptor;
    private final BlindIndexer blindIndexer;

    UserPersistenceAdapter(final UserJpaRepository jpaRepository,
                           final UserPersistenceMapper mapper,
                           final AesGcmEncryptor encryptor,
                           final BlindIndexer blindIndexer) {
        this.jpaRepository = jpaRepository;
        this.mapper = mapper;
        this.encryptor = encryptor;
        this.blindIndexer = blindIndexer;
    }

    @Override
    @Transactional(readOnly = true)
    public Optional<User> findByProviderAndProviderId(String provider, String providerId) {
        return jpaRepository.findByProviderAndProviderId(provider, providerId)
                .map(mapper::toDomain);
    }

    @Override
    @Transactional(readOnly = true)
    public Optional<User> findByEmailHash(String emailHash) {
        return jpaRepository.findByEmailHash(emailHash)
                .map(mapper::toDomain);
    }

    @Override
    @Transactional(readOnly = true)
    public Optional<User> findByEmail(String email) {
        if (email == null) {
            return Optional.empty();
        }
        return jpaRepository.findByEmailHash(blindIndexer.index(email, "email"))
                .map(mapper::toDomain);
    }

    @Override
    @Transactional(readOnly = true)
    public Optional<User> findById(Long id) {
        return jpaRepository.findById(id).map(mapper::toDomain);
    }

    @Override
    public User save(User user) {
        UserJpaEntity entity;
        if (user.getId() != null) {
            // 기존 유저: id 확정 상태에서 암호화
            entity = jpaRepository.findById(user.getId())
                    .map(e -> mapper.updateEntity(e, user))
                    .orElseGet(() -> mapper.toEntity(user));
            return mapper.toDomain(jpaRepository.save(entity));
        } else {
            // ─── 신규 유저: 2-phase save ──────────────────────────────────────
            // 두 단계 모두 클래스 레벨 @Transactional 하나의 트랜잭션 안에서 실행된다.
            // 2단계 재암호화 전 예외가 발생하면 1단계 INSERT를 포함한 전체 트랜잭션이
            // 롤백되므로 AAD=0L placeholder 암호문이 커밋되는 일은 없다.
            //
            // 1단계: GenerationType.IDENTITY로 id 채번 (flush → INSERT 강제)
            entity = mapper.toEntity(user);
            UserJpaEntity saved = jpaRepository.save(entity);
            jpaRepository.flush();

            // 2단계 — 실제 userId(AAD)로 재암호화
            long realUserId = saved.getId();
            saved.updateProfile(
                    encryptor.encrypt(user.getEmail(), realUserId),
                    blindIndexer.index(user.getEmail(), "email"),
                    user.getNickname()
            );
            saved.updatePhone(
                    encryptor.encrypt(user.getPhoneNumber(), realUserId),
                    blindIndexer.index(user.getPhoneNumber(), "phone")
            );
            return mapper.toDomain(jpaRepository.save(saved));
        }
    }
}
