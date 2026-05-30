package dev.jazzybyte.onseoul.user.application;

import dev.jazzybyte.onseoul.user.domain.User;
import dev.jazzybyte.onseoul.user.domain.UserStatus;
import dev.jazzybyte.onseoul.user.port.in.MeResult;
import dev.jazzybyte.onseoul.user.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.OffsetDateTime;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class GetMeServiceTest {

    @Mock
    private LoadUserPort loadUserPort;

    private GetMeService service;

    @BeforeEach
    void setUp() {
        service = new GetMeService(loadUserPort);
    }

    private User user(long id, UserStatus status) {
        return new User(id, "kakao", "kakao-" + id, "user@example.com", "테스트유저",
                status, OffsetDateTime.now(), OffsetDateTime.now());
    }

    @Test
    @DisplayName("ACTIVE 사용자 조회 시 MeResult를 반환한다")
    void getMe_activeUser_returnsResult() {
        when(loadUserPort.findById(1L)).thenReturn(Optional.of(user(1L, UserStatus.ACTIVE)));

        MeResult result = service.getMe(1L);

        assertThat(result.id()).isEqualTo(1L);
        assertThat(result.nickname()).isEqualTo("테스트유저");
        assertThat(result.status()).isEqualTo(UserStatus.ACTIVE);
    }

    @Test
    @DisplayName("userId에 해당하는 사용자가 없으면 UNAUTHORIZED 예외를 던진다")
    void getMe_userNotFound_throwsUnauthorized() {
        when(loadUserPort.findById(99L)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.getMe(99L))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.UNAUTHORIZED));
    }

    @Test
    @DisplayName("SUSPENDED 사용자 조회 시 FORBIDDEN 예외를 던진다")
    void getMe_suspendedUser_throwsForbidden() {
        when(loadUserPort.findById(2L)).thenReturn(Optional.of(user(2L, UserStatus.SUSPENDED)));

        assertThatThrownBy(() -> service.getMe(2L))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.FORBIDDEN));
    }

    @Test
    @DisplayName("DELETED 사용자 조회 시 FORBIDDEN 예외를 던진다")
    void getMe_deletedUser_throwsForbidden() {
        when(loadUserPort.findById(3L)).thenReturn(Optional.of(user(3L, UserStatus.DELETED)));

        assertThatThrownBy(() -> service.getMe(3L))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.FORBIDDEN));
    }
}
