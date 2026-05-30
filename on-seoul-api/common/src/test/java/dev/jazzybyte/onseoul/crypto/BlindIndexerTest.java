package dev.jazzybyte.onseoul.crypto;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class BlindIndexerTest {

    // 32 바이트 hex (64자)
    private static final String BLIND_KEY_HEX = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2";
    private BlindIndexer indexer;

    @BeforeEach
    void setUp() {
        indexer = new BlindIndexer(BLIND_KEY_HEX);
    }

    @Test
    @DisplayName("index() 결과는 64자 소문자 hex")
    void index_returns64CharLowercaseHex() {
        String result = indexer.index("user@EXAMPLE.COM", "email");
        assertThat(result).hasSize(64).matches("[0-9a-f]+");
    }

    @Test
    @DisplayName("email 정규화: 대소문자·공백 무시 — 동일 결과")
    void index_email_caseAndTrimInsensitive() {
        String a = indexer.index("User@Seoul.GO.KR", "email");
        String b = indexer.index("  user@seoul.go.kr  ", "email");
        assertThat(a).isEqualTo(b);
    }

    @Test
    @DisplayName("phone 정규화: 숫자 이외 문자 제거 — 동일 결과")
    void index_phone_stripsNonDigits() {
        String a = indexer.index("010-1234-5678", "phone");
        String b = indexer.index("01012345678", "phone");
        assertThat(a).isEqualTo(b);
    }

    @Test
    @DisplayName("도메인 분리자: email과 phone은 다른 결과")
    void index_emailVsPhone_distinctResults() {
        // '01012345678' 을 email/phone 각각으로 인덱싱하면 달라야 한다
        String emailIdx = indexer.index("01012345678", "email");
        String phoneIdx = indexer.index("01012345678", "phone");
        assertThat(emailIdx).isNotEqualTo(phoneIdx);
    }

    @Test
    @DisplayName("null 입력 → null 반환")
    void index_null_returnsNull() {
        assertThat(indexer.index(null, "email")).isNull();
    }

    @Test
    @DisplayName("같은 입력 → 결정적(deterministic) 결과")
    void index_deterministicResult() {
        String a = indexer.index("hong@example.com", "email");
        String b = indexer.index("hong@example.com", "email");
        assertThat(a).isEqualTo(b);
    }

    @Test
    @DisplayName("phone 정규화: '+' 기호는 유지된다 (국제번호 형식)")
    void index_phone_retainsPlus() {
        String a = indexer.index("+82-10-1234-5678", "phone");
        String b = indexer.index("+821012345678", "phone");
        assertThat(a).isEqualTo(b);
    }

    @Test
    @DisplayName("키 길이 오류(32바이트 미만) → IllegalArgumentException")
    void constructor_shortKey_throwsIllegalArgumentException() {
        String shortKeyHex = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6";  // 16바이트
        assertThatThrownBy(() -> new BlindIndexer(shortKeyHex))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("32 bytes");
    }

    @Test
    @DisplayName("알 수 없는 type 입력 → IllegalArgumentException")
    void index_unknownType_throwsIllegalArgumentException() {
        assertThatThrownBy(() -> indexer.index("value", "ssn"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Unknown type");
    }
}
