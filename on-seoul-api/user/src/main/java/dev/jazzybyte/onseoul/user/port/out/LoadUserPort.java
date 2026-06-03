package dev.jazzybyte.onseoul.user.port.out;

import dev.jazzybyte.onseoul.user.domain.User;

import java.util.Optional;

public interface LoadUserPort {
    Optional<User> findByProviderAndProviderId(String provider, String providerId);
    Optional<User> findById(Long id);
    /** Blind Index로 이메일 조회 (로그인/중복체크). */
    Optional<User> findByEmailHash(String emailHash);

    /**
     * 원문 이메일로 사용자를 조회한다. Blind Index 계산은 어댑터 내부에서 수행한다.
     * 이메일 중복(다른 벤더 가입) 선검사에 사용한다. null 입력 시 빈 Optional 반환.
     */
    Optional<User> findByEmail(String email);
}
