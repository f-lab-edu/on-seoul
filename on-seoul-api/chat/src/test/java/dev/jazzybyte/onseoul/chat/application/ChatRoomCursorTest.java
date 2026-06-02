package dev.jazzybyte.onseoul.chat.application;

import dev.jazzybyte.onseoul.chat.port.out.RoomCursor;
import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Base64;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class ChatRoomCursorTest {

    @Test
    @DisplayName("encode → decode 왕복 시 동일한 updatedAt·id를 반환한다")
    void encodeAndDecode_roundTrip_returnsSameValues() {
        OffsetDateTime updatedAt = OffsetDateTime.of(2026, 6, 1, 10, 30, 0, 0, ZoneOffset.UTC);
        Long id = 42L;

        String token = ChatRoomCursor.encode(updatedAt, id);
        RoomCursor cursor = ChatRoomCursor.decode(token);

        assertThat(cursor.id()).isEqualTo(id);
        assertThat(cursor.updatedAt().toInstant().toEpochMilli())
                .isEqualTo(updatedAt.toInstant().toEpochMilli());
    }

    @Test
    @DisplayName("decode(null) → INVALID_INPUT 예외")
    void decode_null_throwsInvalidInput() {
        assertThatThrownBy(() -> ChatRoomCursor.decode(null))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.INVALID_INPUT));
    }

    @Test
    @DisplayName("decode('') → INVALID_INPUT 예외")
    void decode_emptyString_throwsInvalidInput() {
        assertThatThrownBy(() -> ChatRoomCursor.decode(""))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.INVALID_INPUT));
    }

    @Test
    @DisplayName("decode(구분자 없는 문자열) → INVALID_INPUT 예외")
    void decode_noSeparator_throwsInvalidInput() {
        // Base64 encode a string without '_'
        String noSep = Base64.getUrlEncoder().withoutPadding().encodeToString("1234567890".getBytes());
        assertThatThrownBy(() -> ChatRoomCursor.decode(noSep))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.INVALID_INPUT));
    }

    @Test
    @DisplayName("decode(epochMilli가 숫자가 아닌 문자열) → INVALID_INPUT 예외")
    void decode_nonNumericEpochMilli_throwsInvalidInput() {
        // Raw: "abc_123" — epochMilli 부분이 숫자가 아님
        String token = Base64.getUrlEncoder().withoutPadding().encodeToString("abc_123".getBytes());
        assertThatThrownBy(() -> ChatRoomCursor.decode(token))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.INVALID_INPUT));
    }

    @Test
    @DisplayName("decode(blank string) → INVALID_INPUT 예외")
    void decode_blankString_throwsInvalidInput() {
        assertThatThrownBy(() -> ChatRoomCursor.decode("   "))
                .isInstanceOf(OnSeoulApiException.class)
                .satisfies(ex -> assertThat(((OnSeoulApiException) ex).getErrorCode())
                        .isEqualTo(ErrorCode.INVALID_INPUT));
    }
}
