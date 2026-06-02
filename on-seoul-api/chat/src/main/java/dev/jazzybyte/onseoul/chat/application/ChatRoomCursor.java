package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.port.out.RoomCursor;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Base64;

/**
 * ChatRoom 커서 페이지네이션 인코딩/디코딩 유틸.
 *
 * <p>커서 포맷: {@code "{epochMilli}_{id}"} → Base64 URL-safe (패딩 없음)</p>
 */
public final class ChatRoomCursor {

    private static final Base64.Encoder ENCODER = Base64.getUrlEncoder().withoutPadding();
    private static final Base64.Decoder DECODER = Base64.getUrlDecoder();

    private ChatRoomCursor() {}

    public static String encode(OffsetDateTime updatedAt, Long id) {
        String raw = updatedAt.toInstant().toEpochMilli() + "_" + id;
        return ENCODER.encodeToString(raw.getBytes());
    }

    public static RoomCursor decode(String token) {
        if (token == null || token.isBlank()) {
            throw new OnSeoulApiException(ErrorCode.INVALID_INPUT);
        }
        try {
            String raw = new String(DECODER.decode(token));
            int sep = raw.lastIndexOf('_');
            if (sep <= 0) {
                throw new OnSeoulApiException(ErrorCode.INVALID_INPUT);
            }
            long epochMilli = Long.parseLong(raw.substring(0, sep));
            long id = Long.parseLong(raw.substring(sep + 1));
            OffsetDateTime updatedAt = Instant.ofEpochMilli(epochMilli).atOffset(ZoneOffset.UTC);
            return new RoomCursor(updatedAt, id);
        } catch (OnSeoulApiException e) {
            throw e;
        } catch (Exception e) {
            throw new OnSeoulApiException(ErrorCode.INVALID_INPUT, "커서 디코딩 실패", e);
        }
    }
}
