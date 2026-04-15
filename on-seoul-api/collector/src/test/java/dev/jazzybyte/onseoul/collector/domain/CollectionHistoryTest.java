package dev.jazzybyte.onseoul.collector.domain;

import dev.jazzybyte.onseoul.collector.enums.CollectionStatus;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;

class CollectionHistoryTest {

    private CollectionHistory history;

    @BeforeEach
    void setUp() {
        history = CollectionHistory.builder()
                .sourceId(1L)
                .build();
    }

    @Test
    @DisplayName("생성 직후 status는 FAILED이다")
    void initial_status_is_failed() {
        assertThat(history.getStatus()).isEqualTo(CollectionStatus.FAILED);
    }

    @Nested
    @DisplayName("complete()")
    class Complete {

        @Test
        @DisplayName("status가 SUCCESS로 변경된다")
        void status_becomes_success() {
            history.complete(100, 10, 5, 2, 3000);

            assertThat(history.getStatus()).isEqualTo(CollectionStatus.SUCCESS);
        }

        @Test
        @DisplayName("수집 건수 통계가 저장된다")
        void collection_counts_are_saved() {
            history.complete(100, 10, 5, 2, 3000);

            assertThat(history.getTotalFetched()).isEqualTo(100);
            assertThat(history.getNewCount()).isEqualTo(10);
            assertThat(history.getUpdatedCount()).isEqualTo(5);
            assertThat(history.getDeletedCount()).isEqualTo(2);
        }

        @Test
        @DisplayName("소요 시간이 저장된다")
        void duration_is_saved() {
            history.complete(100, 10, 5, 2, 3000);

            assertThat(history.getDurationMs()).isEqualTo(3000);
        }

        @Test
        @DisplayName("errorMessage는 null로 유지된다")
        void error_message_remains_null() {
            history.complete(100, 10, 5, 2, 3000);

            assertThat(history.getErrorMessage()).isNull();
        }
    }

    @Nested
    @DisplayName("fail()")
    class Fail {

        @Test
        @DisplayName("status가 FAILED로 변경된다")
        void status_becomes_failed() {
            history.fail("Connection timeout", 1500);

            assertThat(history.getStatus()).isEqualTo(CollectionStatus.FAILED);
        }

        @Test
        @DisplayName("에러 메시지가 저장된다")
        void error_message_is_saved() {
            history.fail("Connection timeout", 1500);

            assertThat(history.getErrorMessage()).isEqualTo("Connection timeout");
        }

        @Test
        @DisplayName("소요 시간이 저장된다")
        void duration_is_saved() {
            history.fail("Connection timeout", 1500);

            assertThat(history.getDurationMs()).isEqualTo(1500);
        }
    }

    @Nested
    @DisplayName("partial()")
    class Partial {

        @Test
        @DisplayName("status가 PARTIAL로 변경된다")
        void status_becomes_partial() {
            history.partial(80, 8, 3, 1, 2500, "OA-2270 응답 오류");

            assertThat(history.getStatus()).isEqualTo(CollectionStatus.PARTIAL);
        }

        @Test
        @DisplayName("성공한 수집 건수 통계가 저장된다")
        void partial_counts_are_saved() {
            history.partial(80, 8, 3, 1, 2500, "OA-2270 응답 오류");

            assertThat(history.getTotalFetched()).isEqualTo(80);
            assertThat(history.getNewCount()).isEqualTo(8);
            assertThat(history.getUpdatedCount()).isEqualTo(3);
            assertThat(history.getDeletedCount()).isEqualTo(1);
        }

        @Test
        @DisplayName("에러 메시지와 소요 시간이 함께 저장된다")
        void error_message_and_duration_are_saved() {
            history.partial(80, 8, 3, 1, 2500, "OA-2270 응답 오류");

            assertThat(history.getErrorMessage()).isEqualTo("OA-2270 응답 오류");
            assertThat(history.getDurationMs()).isEqualTo(2500);
        }
    }
}
