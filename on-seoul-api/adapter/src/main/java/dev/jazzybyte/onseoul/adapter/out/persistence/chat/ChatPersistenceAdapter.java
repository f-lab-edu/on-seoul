package dev.jazzybyte.onseoul.adapter.out.persistence.chat;

import dev.jazzybyte.onseoul.domain.model.ChatMessage;
import dev.jazzybyte.onseoul.domain.model.ChatRoom;
import dev.jazzybyte.onseoul.domain.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.domain.port.out.SaveChatMessagePort;
import dev.jazzybyte.onseoul.domain.port.out.SaveChatRoomPort;
import org.springframework.stereotype.Component;

import java.util.Optional;

@Component
public class ChatPersistenceAdapter implements SaveChatRoomPort, LoadChatRoomPort, SaveChatMessagePort {

    private final ChatRoomJpaRepository chatRoomJpaRepository;
    private final ChatMessageJpaRepository chatMessageJpaRepository;

    public ChatPersistenceAdapter(final ChatRoomJpaRepository chatRoomJpaRepository,
                                  final ChatMessageJpaRepository chatMessageJpaRepository) {
        this.chatRoomJpaRepository = chatRoomJpaRepository;
        this.chatMessageJpaRepository = chatMessageJpaRepository;
    }

    @Override
    public ChatRoom save(ChatRoom room) {
        ChatRoomJpaEntity entity = ChatRoomJpaEntity.fromDomain(room);
        return chatRoomJpaRepository.save(entity).toDomain();
    }

    @Override
    public Optional<ChatRoom> findById(Long id) {
        return chatRoomJpaRepository.findById(id)
                .map(ChatRoomJpaEntity::toDomain);
    }

    @Override
    public ChatMessage save(ChatMessage message) {
        ChatMessageJpaEntity entity = ChatMessageJpaEntity.fromDomain(message);
        return chatMessageJpaRepository.save(entity).toDomain();
    }

    @Override
    public Long nextSeq() {
        return chatMessageJpaRepository.nextSeq();
    }
}
