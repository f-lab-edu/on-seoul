package dev.jazzybyte.onseoul.domain.port.in;

public interface GetMeUseCase {
    MeResult getMe(Long userId);
}
