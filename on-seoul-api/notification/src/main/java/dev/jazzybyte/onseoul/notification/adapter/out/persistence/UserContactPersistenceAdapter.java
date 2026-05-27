package dev.jazzybyte.onseoul.notification.adapter.out.persistence;

import dev.jazzybyte.onseoul.crypto.AesGcmEncryptor;
import dev.jazzybyte.onseoul.notification.domain.UserContact;
import dev.jazzybyte.onseoul.notification.port.out.LoadUserContactPort;
import org.jooq.DSLContext;
import org.springframework.stereotype.Component;

import java.util.Optional;

import static dev.jazzybyte.onseoul.jooq.Tables.USERS;

/**
 * users 테이블에서 알림 발송에 필요한 연락처를 조회한 후 복호화하여 반환한다.
 *
 * <p>user BC JPA 엔티티를 직접 import하지 않고 jOOQ DSLContext로 처리한다.
 * 암호화 컬럼(email_enc, phone_enc)을 조회하고 {@link AesGcmEncryptor}로 복호화한다.
 * AesGcmEncryptor는 common 모듈에 위치하므로 BC 경계를 위반하지 않는다.
 */
@Component
class UserContactPersistenceAdapter implements LoadUserContactPort {

    private final DSLContext dsl;
    private final AesGcmEncryptor encryptor;

    UserContactPersistenceAdapter(DSLContext dsl, AesGcmEncryptor encryptor) {
        this.dsl = dsl;
        this.encryptor = encryptor;
    }

    @Override
    public Optional<UserContact> loadContact(Long userId) {
        return dsl.select(USERS.ID, USERS.EMAIL_ENC, USERS.PHONE_ENC)
                .from(USERS)
                .where(USERS.ID.eq(userId))
                .fetchOptional(r -> {
                    long id = r.get(USERS.ID);
                    String emailEnc = r.get(USERS.EMAIL_ENC);
                    String phoneEnc = r.get(USERS.PHONE_ENC);
                    return new UserContact(
                            id,
                            encryptor.decrypt(emailEnc, id),
                            encryptor.decrypt(phoneEnc, id)
                    );
                });
    }
}
