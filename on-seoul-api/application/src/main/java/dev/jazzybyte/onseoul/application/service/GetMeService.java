package dev.jazzybyte.onseoul.application.service;

import dev.jazzybyte.onseoul.domain.model.User;
import dev.jazzybyte.onseoul.domain.model.UserStatus;
import dev.jazzybyte.onseoul.domain.port.in.GetMeUseCase;
import dev.jazzybyte.onseoul.domain.port.in.MeResult;
import dev.jazzybyte.onseoul.domain.port.out.LoadUserPort;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@RequiredArgsConstructor
public class GetMeService implements GetMeUseCase {

    private final LoadUserPort loadUserPort;

    @Override
    public MeResult getMe(Long userId) {
        User user = loadUserPort.findById(userId)
                .orElseThrow(() -> new OnSeoulApiException(ErrorCode.UNAUTHORIZED));

        if (user.getStatus() != UserStatus.ACTIVE) {
            log.warn("[Auth] 비활성 계정 접근 차단 - userId={}, status={}", userId, user.getStatus());
            throw new OnSeoulApiException(ErrorCode.FORBIDDEN);
        }

        return new MeResult(user.getId(), user.getNickname(), user.getStatus());
    }
}
