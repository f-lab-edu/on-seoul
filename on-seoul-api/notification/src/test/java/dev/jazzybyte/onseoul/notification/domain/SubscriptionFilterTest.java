package dev.jazzybyte.onseoul.notification.domain;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.HashSet;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * SubscriptionFilter record 단위 테스트 (ADR-0004 §SubscriptionFilter).
 *
 * <p>null/빈 Set 정규화 및 isEmpty/empty 헬퍼 동작을 검증한다.
 */
class SubscriptionFilterTest {

    @Test
    @DisplayName("empty() — 모든 필드가 비어 있으며 isEmpty()=true")
    void empty_returnsAllEmptyFilter() {
        SubscriptionFilter f = SubscriptionFilter.empty();

        assertThat(f.statuses()).isEmpty();
        assertThat(f.areaNames()).isEmpty();
        assertThat(f.maxClassNames()).isEmpty();
        assertThat(f.isEmpty()).isTrue();
    }

    @Test
    @DisplayName("compact constructor — null Set은 빈 Set으로 정규화된다")
    void nulls_areNormalizedToEmptySets() {
        SubscriptionFilter f = new SubscriptionFilter(null, null, null);

        assertThat(f.statuses()).isEmpty();
        assertThat(f.areaNames()).isEmpty();
        assertThat(f.maxClassNames()).isEmpty();
        assertThat(f.isEmpty()).isTrue();
    }

    @Test
    @DisplayName("isEmpty() — 하나라도 값이 있으면 false")
    void isEmpty_falseWhenAnyFieldPopulated() {
        SubscriptionFilter onlyStatuses = new SubscriptionFilter(Set.of("RECEIVING"), Set.of(), Set.of());
        SubscriptionFilter onlyArea     = new SubscriptionFilter(Set.of(), Set.of("강남구"), Set.of());
        SubscriptionFilter onlyCategory = new SubscriptionFilter(Set.of(), Set.of(), Set.of("문화행사"));

        assertThat(onlyStatuses.isEmpty()).isFalse();
        assertThat(onlyArea.isEmpty()).isFalse();
        assertThat(onlyCategory.isEmpty()).isFalse();
    }

    @Test
    @DisplayName("compact constructor — 결과 Set은 변경 불가능(immutable)")
    void normalizedSets_areUnmodifiable() {
        SubscriptionFilter f = new SubscriptionFilter(
                new HashSet<>(Set.of("A")), new HashSet<>(Set.of("B")), new HashSet<>(Set.of("C")));

        assertThatThrownBy(() -> f.statuses().add("X"))
                .isInstanceOf(UnsupportedOperationException.class);
        assertThatThrownBy(() -> f.areaNames().add("Y"))
                .isInstanceOf(UnsupportedOperationException.class);
        assertThatThrownBy(() -> f.maxClassNames().add("Z"))
                .isInstanceOf(UnsupportedOperationException.class);
    }

    @Test
    @DisplayName("compact constructor — 원본 입력 변형은 결과 필드에 반영되지 않는다 (방어적 복사)")
    void normalizedSets_areDefensiveCopies() {
        Set<String> source = new HashSet<>(Set.of("RECEIVING"));
        SubscriptionFilter f = new SubscriptionFilter(source, Set.of(), Set.of());

        source.add("CLOSED");

        assertThat(f.statuses()).containsExactly("RECEIVING");
    }
}
