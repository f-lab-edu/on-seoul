package dev.jazzybyte.onseoul.user.application;

import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.user.domain.User;
import dev.jazzybyte.onseoul.user.port.in.SocialLoginCommand;
import dev.jazzybyte.onseoul.user.port.in.UpdateContactUseCase.UpdateContactCommand;
import dev.jazzybyte.onseoul.user.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.user.port.out.SaveUserPort;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class UpdateContactServiceTest {

    @Mock
    private LoadUserPort loadUserPort;

    @Mock
    private SaveUserPort saveUserPort;

    private UpdateContactService service;

    @BeforeEach
    void setUp() {
        service = new UpdateContactService(loadUserPort, saveUserPort);
    }

    @Test
    @DisplayName("updateContact() — 정상 케이스: phoneNumber 갱신 후 저장")
    void updateContact_existingUser_updatesAndSaves() {
        User user = User.create(new SocialLoginCommand("google", "uid-1", "u@x.com", "닉"));
        when(loadUserPort.findById(1L)).thenReturn(Optional.of(user));
        when(saveUserPort.save(any())).thenAnswer(inv -> inv.getArgument(0));

        service.updateContact(1L, new UpdateContactCommand("010-1234-5678"));

        ArgumentCaptor<User> captor = ArgumentCaptor.forClass(User.class);
        verify(saveUserPort).save(captor.capture());
        assertThat(captor.getValue().getPhoneNumber()).isEqualTo("010-1234-5678");
    }

    @Test
    @DisplayName("updateContact() — 존재하지 않는 userId → USER_NOT_FOUND 예외")
    void updateContact_nonExistentUser_throwsException() {
        when(loadUserPort.findById(99L)).thenReturn(Optional.empty());

        assertThatThrownBy(() ->
                service.updateContact(99L, new UpdateContactCommand("010-0000-0000")))
                .isInstanceOf(OnSeoulApiException.class);

        verify(saveUserPort, never()).save(any());
    }

    @Test
    @DisplayName("updateContact() — phoneNumber null 허용")
    void updateContact_nullPhoneNumber_saves() {
        User user = User.create(new SocialLoginCommand("google", "uid-2", "u2@x.com", "닉2"));
        when(loadUserPort.findById(2L)).thenReturn(Optional.of(user));
        when(saveUserPort.save(any())).thenAnswer(inv -> inv.getArgument(0));

        service.updateContact(2L, new UpdateContactCommand(null));

        ArgumentCaptor<User> captor = ArgumentCaptor.forClass(User.class);
        verify(saveUserPort).save(captor.capture());
        assertThat(captor.getValue().getPhoneNumber()).isNull();
    }
}
