package dev.jazzybyte.onseoul.domain;

/**
 * 사용자 계정 상태.
 *
 * <p>DB schema CHECK 제약과 동일한 값을 유지한다: ACTIVE, SUSPENDED, DELETED</p>
 */
public enum UserStatus {
    ACTIVE,
    SUSPENDED,
    DELETED;

    public boolean isActive() {
        return this == ACTIVE;
    }
}
