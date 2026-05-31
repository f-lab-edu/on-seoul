package dev.jazzybyte.onseoul.user.port.out;

import dev.jazzybyte.onseoul.user.domain.User;

import java.util.Optional;

public interface LoadUserPort {
    Optional<User> findByProviderAndProviderId(String provider, String providerId);
    Optional<User> findById(Long id);
    /** Blind Index로 이메일 조회 (로그인/중복체크). */
    Optional<User> findByEmailHash(String emailHash);
}
