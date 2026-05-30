package dev.jazzybyte.onseoul.user.adapter.out.persistence;

import dev.jazzybyte.onseoul.crypto.AesGcmEncryptor;
import dev.jazzybyte.onseoul.crypto.BlindIndexer;
import dev.jazzybyte.onseoul.user.domain.User;
import org.springframework.stereotype.Component;

@Component
class UserPersistenceMapper {

    private final AesGcmEncryptor encryptor;
    private final BlindIndexer blindIndexer;

    UserPersistenceMapper(AesGcmEncryptor encryptor, BlindIndexer blindIndexer) {
        this.encryptor = encryptor;
        this.blindIndexer = blindIndexer;
    }

    /**
     * JPA 엔티티 → 도메인 모델. email/phone 복호화 수행.
     * userId(AAD)는 엔티티의 id로부터 가져온다.
     */
    User toDomain(UserJpaEntity entity) {
        long userId = entity.getId();
        return new User(
                entity.getId(),
                entity.getProvider(),
                entity.getProviderId(),
                encryptor.decrypt(entity.getEmailEnc(), userId),
                entity.getNickname(),
                encryptor.decrypt(entity.getPhoneEnc(), userId),
                entity.getStatus(),
                entity.getCreatedAt(),
                entity.getUpdatedAt()
        );
    }

    /**
     * 도메인 모델 → JPA 엔티티 (신규). 저장 전 암호화.
     * 신규 엔티티는 id가 없으므로 AAD로 0L을 사용하고,
     * save() 후 id 채번이 완료되면 profile 업데이트를 통해 재암호화된다.
     *
     * <p>주의: 신규 사용자의 AAD는 DB 채번 이전이므로 영속화 직후
     * {@link UserPersistenceAdapter#save(User)} 에서 id 기반 재암호화를 수행한다.
     */
    UserJpaEntity toEntity(User user) {
        // 신규 엔티티 — id 미확정 상태이므로 placeholder AAD(0L) 사용.
        // save() 반환 후 재암호화 로직은 UserPersistenceAdapter에서 처리.
        return new UserJpaEntity(
                user.getProvider(),
                user.getProviderId(),
                encryptor.encrypt(user.getEmail(), 0L),
                blindIndexer.index(user.getEmail(), "email"),
                user.getNickname(),
                user.getStatus()
        );
    }

    /**
     * 기존 엔티티 필드 갱신. id 확정 상태이므로 userId AAD 사용.
     */
    UserJpaEntity updateEntity(UserJpaEntity entity, User user) {
        long userId = entity.getId();
        entity.updateProfile(
                encryptor.encrypt(user.getEmail(), userId),
                blindIndexer.index(user.getEmail(), "email"),
                user.getNickname()
        );
        entity.updatePhone(
                encryptor.encrypt(user.getPhoneNumber(), userId),
                blindIndexer.index(user.getPhoneNumber(), "phone")
        );
        entity.updateStatus(user.getStatus());
        return entity;
    }
}
