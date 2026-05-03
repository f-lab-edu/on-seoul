package dev.jazzybyte.onseoul.domain.port.out;

import dev.jazzybyte.onseoul.domain.model.User;

public interface SaveUserPort {
    User save(User user);
}
