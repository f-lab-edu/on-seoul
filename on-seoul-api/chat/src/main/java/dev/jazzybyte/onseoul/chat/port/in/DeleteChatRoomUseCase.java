package dev.jazzybyte.onseoul.chat.port.in;

public interface DeleteChatRoomUseCase {

    void delete(Long userId, Long roomId);
}
