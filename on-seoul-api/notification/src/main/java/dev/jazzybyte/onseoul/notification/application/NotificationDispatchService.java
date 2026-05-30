package dev.jazzybyte.onseoul.notification.application;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.notification.domain.NotificationDispatch;
import dev.jazzybyte.onseoul.notification.port.in.ListDispatchesUseCase;
import dev.jazzybyte.onseoul.notification.port.out.LoadDispatchPort;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
@RequiredArgsConstructor
public class NotificationDispatchService implements ListDispatchesUseCase {

    static final int DEFAULT_SIZE = 20;
    static final int MAX_SIZE = 100;

    private final LoadDispatchPort loadDispatchPort;

    @Override
    @Transactional(readOnly = true)
    public DispatchPage list(Long userId, Long cursor, int size) {
        int normalizedSize = normalizeSize(size);
        List<NotificationDispatch> dispatches = loadDispatchPort.loadByUserId(userId, cursor, normalizedSize);
        Long nextCursor = dispatches.isEmpty() || dispatches.size() < normalizedSize
                ? null
                : dispatches.get(dispatches.size() - 1).getId();
        return new DispatchPage(dispatches, nextCursor);
    }

    private int normalizeSize(int size) {
        if (size <= 0) return DEFAULT_SIZE;
        if (size > MAX_SIZE) {
            throw new OnSeoulApiException(ErrorCode.INVALID_INPUT,
                    "size 는 1..100 범위여야 합니다. 요청값=" + size);
        }
        return size;
    }
}
