package dev.jazzybyte.onseoul.user.adapter.out.persistence;

import dev.jazzybyte.onseoul.user.domain.UserStatus;
import jakarta.persistence.*;
import lombok.AccessLevel;
import lombok.Getter;
import lombok.NoArgsConstructor;
import org.hibernate.annotations.UpdateTimestamp;

import java.time.OffsetDateTime;

@Entity
@Table(name = "users")
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
class UserJpaEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "provider", nullable = false, length = 20)
    private String provider;

    @Column(name = "provider_id", nullable = false, length = 100)
    private String providerId;

    /** AES-256-GCM 암호문. 형식: {@code v1:<base64(nonce+cipher+tag)>} */
    @Column(name = "email_enc", columnDefinition = "TEXT")
    private String emailEnc;

    /** HMAC-SHA256 Blind Index (hex 64자). WHERE 절 이메일 조회용. */
    @Column(name = "email_hash", length = 64)
    private String emailHash;

    @Column(name = "nickname", length = 100)
    private String nickname;

    /** AES-256-GCM 암호문. 형식: {@code v1:<base64(nonce+cipher+tag)>} */
    @Column(name = "phone_enc", columnDefinition = "TEXT")
    private String phoneEnc;

    /** HMAC-SHA256 Blind Index (hex 64자). WHERE 절 전화번호 조회용. */
    @Column(name = "phone_hash", length = 64)
    private String phoneHash;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 20)
    private UserStatus status;

    @Column(name = "created_at", nullable = false, updatable = false,
            columnDefinition = "TIMESTAMPTZ DEFAULT NOW()")
    private OffsetDateTime createdAt;

    @UpdateTimestamp
    @Column(name = "updated_at", nullable = false, columnDefinition = "TIMESTAMPTZ DEFAULT NOW()")
    private OffsetDateTime updatedAt;

    UserJpaEntity(String provider, String providerId,
                  String emailEnc, String emailHash,
                  String nickname, UserStatus status) {
        this.provider = provider;
        this.providerId = providerId;
        this.emailEnc = emailEnc;
        this.emailHash = emailHash;
        this.nickname = nickname;
        this.status = status;
        this.createdAt = OffsetDateTime.now();
    }

    void updateProfile(String emailEnc, String emailHash, String nickname) {
        this.emailEnc = emailEnc;
        this.emailHash = emailHash;
        this.nickname = nickname;
    }

    void updatePhone(String phoneEnc, String phoneHash) {
        this.phoneEnc = phoneEnc;
        this.phoneHash = phoneHash;
    }

    void updateStatus(UserStatus status) {
        this.status = status;
    }
}
