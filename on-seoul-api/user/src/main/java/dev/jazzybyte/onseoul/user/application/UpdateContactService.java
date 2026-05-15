package dev.jazzybyte.onseoul.user.application;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.user.domain.User;
import dev.jazzybyte.onseoul.user.port.in.UpdateContactUseCase;
import dev.jazzybyte.onseoul.user.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.user.port.out.SaveUserPort;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RequiredArgsConstructor
public class UpdateContactService implements UpdateContactUseCase {

    private final LoadUserPort loadUserPort;
    private final SaveUserPort saveUserPort;

    @Override
    @Transactional
    public void updateContact(Long userId, UpdateContactCommand command) {
        User user = loadUserPort.findById(userId)
                .orElseThrow(() -> new OnSeoulApiException(ErrorCode.USER_NOT_FOUND));
        user.updateContact(command.phoneNumber());
        saveUserPort.save(user);
    }
}
