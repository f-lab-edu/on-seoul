package dev.jazzybyte.onseoul.user.port.in;

import jakarta.validation.constraints.Size;

public interface UpdateContactUseCase {
    void updateContact(Long userId, UpdateContactCommand command);

    record UpdateContactCommand(@Size(max = 20) String phoneNumber) {}
}
