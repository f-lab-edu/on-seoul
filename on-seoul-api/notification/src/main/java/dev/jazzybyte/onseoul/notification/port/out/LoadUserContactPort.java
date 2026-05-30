package dev.jazzybyte.onseoul.notification.port.out;

import dev.jazzybyte.onseoul.notification.domain.UserContact;

import java.util.Optional;

public interface LoadUserContactPort {

    /**
     * userId에 해당하는 사용자의 연락처를 조회한다.
     * 사용자가 존재하지 않으면 empty를 반환한다.
     */
    Optional<UserContact> loadContact(Long userId);
}
