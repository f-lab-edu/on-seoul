package dev.jazzybyte.onseoul.chat.adapter.out.persistence;

import dev.jazzybyte.onseoul.chat.domain.ChatMessage;
import dev.jazzybyte.onseoul.chat.domain.ChatRoom;
import dev.jazzybyte.onseoul.chat.port.out.DeleteChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.LoadChatRoomPort;
import dev.jazzybyte.onseoul.chat.port.out.RoomCursor;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatMessagePort;
import dev.jazzybyte.onseoul.chat.port.out.SaveChatRoomPort;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Optional;
import java.util.stream.Collectors;

@Component
public class ChatPersistenceAdapter implements SaveChatRoomPort, LoadChatRoomPort, SaveChatMessagePort,
        LoadChatMessagePort, DeleteChatRoomPort {

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
    public Optional<ChatRoom> findActiveByIdAndUserId(Long id, Long userId) {
        return chatRoomJpaRepository.findByIdAndUserIdAndDeletedAtIsNull(id, userId)
                .map(ChatRoomJpaEntity::toDomain);
    }

    @Override
    public List<ChatRoom> findActiveByUserId(Long userId, RoomCursor cursor, int limit) {
        PageRequest pageable = PageRequest.of(0, limit);
        if (cursor == null) {
            return chatRoomJpaRepository.findFirstPage(userId, pageable)
                    .stream()
                    .map(ChatRoomJpaEntity::toDomain)
                    .toList();
        }
        return chatRoomJpaRepository.findNextPage(userId, cursor.updatedAt(), cursor.id(), pageable)
                .stream()
                .map(ChatRoomJpaEntity::toDomain)
                .toList();
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

    @Override
    public List<ChatMessage> findByRoomIdOrderBySeqAsc(Long roomId) {
        return chatMessageJpaRepository.findByRoomIdOrderBySeqAsc(roomId)
                .stream()
                .map(ChatMessageJpaEntity::toDomain)
                .toList();
    }

    @Override
    public List<ChatMessage> findRecentByRoomIdOrderBySeqAsc(Long roomId, int limit) {
        // 최신 N개를 seq DESC로 읽은 뒤, 과거 → 최신 순서로 뒤집어 반환한다.
        List<ChatMessage> recentDesc = chatMessageJpaRepository
                .findByRoomIdOrderBySeqDesc(roomId, PageRequest.of(0, limit))
                .stream()
                .map(ChatMessageJpaEntity::toDomain)
                .collect(Collectors.toCollection(ArrayList::new));
        Collections.reverse(recentDesc);
        return recentDesc;
    }

    @Override
    public void softDelete(ChatRoom room) {
        chatRoomJpaRepository.save(ChatRoomJpaEntity.fromDomain(room));
    }
}
