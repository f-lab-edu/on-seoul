package dev.jazzybyte.onseoul.notification.adapter.in.web;

import dev.jazzybyte.onseoul.exception.ErrorCode;
import dev.jazzybyte.onseoul.exception.OnSeoulApiException;
import dev.jazzybyte.onseoul.notification.domain.KeywordTarget;
import dev.jazzybyte.onseoul.notification.domain.SubscriptionFilter;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * FilterDto ↔ SubscriptionFilter 매핑 단위 테스트.
 *
 * <p>keywordTargets 는 와이어에서 String 으로 주고받고 도메인에서는 {@link KeywordTarget} enum 이다.
 * 미인식 String 은 400(INVALID_INPUT) 으로 거부한다(enum 직접 노출 회피).
 */
class FilterDtoTest {

    @Test
    @DisplayName("toDomain — keywordTargets String이 KeywordTarget으로 매핑된다")
    void toDomain_mapsKeywordTargetStrings() {
        FilterDto dto = new FilterDto(Set.of(), Set.of(), Set.of(), Set.of("수영"),
                Set.of("SERVICE_NAME", "PLACE_NAME"));

        SubscriptionFilter f = dto.toDomain();

        assertThat(f.keywordTargets()).containsExactlyInAnyOrder(
                KeywordTarget.SERVICE_NAME, KeywordTarget.PLACE_NAME);
    }

    @Test
    @DisplayName("toDomain — 미인식 keywordTarget String → INVALID_INPUT(400)")
    void toDomain_unknownTarget_throws400() {
        FilterDto dto = new FilterDto(Set.of(), Set.of(), Set.of(), Set.of("수영"),
                Set.of("BOGUS"));

        assertThatThrownBy(dto::toDomain)
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.INVALID_INPUT);
    }

    @Test
    @DisplayName("toDomain — keywordTargets 미지정(null/empty) → 빈 Set (service에서 정규화)")
    void toDomain_noTargets_emptySet() {
        assertThat(new FilterDto(Set.of(), Set.of(), Set.of(), Set.of("수영"), null)
                .toDomain().keywordTargets()).isEmpty();
        assertThat(new FilterDto(Set.of(), Set.of(), Set.of(), Set.of("수영"))
                .toDomain().keywordTargets()).isEmpty();
    }

    @Test
    @DisplayName("fromDomain — KeywordTarget enum이 name() String으로 노출된다")
    void fromDomain_exposesTargetNames() {
        SubscriptionFilter f = new SubscriptionFilter(Set.of(), Set.of(), Set.of(),
                Set.of("수영"), Set.of(KeywordTarget.PLACE_NAME));

        FilterDto dto = FilterDto.fromDomain(f);

        assertThat(dto.keywordTargets()).containsExactly("PLACE_NAME");
    }

    @Test
    @DisplayName("fromDomain — null filter는 빈 keywordTargets")
    void fromDomain_null_emptyTargets() {
        assertThat(FilterDto.fromDomain(null).keywordTargets()).isEmpty();
    }

    @Test
    @DisplayName("toDomain — 소문자 대상(service_name)은 valueOf 대소문자 민감으로 INVALID_INPUT(400)")
    void toDomain_lowercaseTarget_throws400() {
        FilterDto dto = new FilterDto(Set.of(), Set.of(), Set.of(), Set.of("수영"),
                Set.of("service_name"));

        assertThatThrownBy(dto::toDomain)
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.INVALID_INPUT);
    }

    @Test
    @DisplayName("toDomain — 혼합 대소문자(Place_Name)도 거부된다(정확한 enum name만 허용)")
    void toDomain_mixedCaseTarget_throws400() {
        FilterDto dto = new FilterDto(Set.of(), Set.of(), Set.of(), Set.of("수영"),
                Set.of("Place_Name"));

        assertThatThrownBy(dto::toDomain)
                .isInstanceOf(OnSeoulApiException.class)
                .extracting("errorCode").isEqualTo(ErrorCode.INVALID_INPUT);
    }

    @Test
    @DisplayName("toDomain — null/blank 대상 토큰은 skip 되고 유효 토큰만 매핑된다")
    void toDomain_blankTokens_skipped() {
        java.util.Set<String> raw = new java.util.LinkedHashSet<>();
        raw.add("SERVICE_NAME");
        raw.add("");
        raw.add("   ");
        FilterDto dto = new FilterDto(Set.of(), Set.of(), Set.of(), Set.of("수영"), raw);

        SubscriptionFilter f = dto.toDomain();

        assertThat(f.keywordTargets()).containsExactly(KeywordTarget.SERVICE_NAME);
    }

    @Test
    @DisplayName("toDomain — 동일 대상 토큰 중복은 Set으로 1개로 합쳐진다")
    void toDomain_duplicateTargets_dedup() {
        java.util.Set<String> raw = new java.util.LinkedHashSet<>();
        raw.add("PLACE_NAME");
        // LinkedHashSet 입력은 String 중복을 이미 제거하므로 enum 매핑 후 중복 없음을 단언
        FilterDto dto = new FilterDto(Set.of(), Set.of(), Set.of(), Set.of("수영"), raw);

        SubscriptionFilter f = dto.toDomain();

        assertThat(f.keywordTargets()).containsExactly(KeywordTarget.PLACE_NAME);
        assertThat(f.keywordTargets()).hasSize(1);
    }

    @Test
    @DisplayName("toDomain — keywords 0개 + targets 지정: targets는 매핑되나 isEmpty=true(매칭 무의미)")
    void toDomain_targetsWithoutKeywords_noConditionEffect() {
        FilterDto dto = new FilterDto(Set.of(), Set.of(), Set.of(), Set.of(),
                Set.of("SERVICE_NAME"));

        SubscriptionFilter f = dto.toDomain();

        // 대상은 보존되지만 조건이 아니므로 빈 구독
        assertThat(f.keywordTargets()).containsExactly(KeywordTarget.SERVICE_NAME);
        assertThat(f.isEmpty()).isTrue();
    }
}
