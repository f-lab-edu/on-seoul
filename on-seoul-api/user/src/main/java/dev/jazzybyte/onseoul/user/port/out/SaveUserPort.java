package dev.jazzybyte.onseoul.user.port.out;

import dev.jazzybyte.onseoul.user.domain.User;

public interface SaveUserPort {
    User save(User user);
}
