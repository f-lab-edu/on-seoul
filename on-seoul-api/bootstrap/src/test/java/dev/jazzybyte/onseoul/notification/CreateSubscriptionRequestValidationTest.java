package dev.jazzybyte.onseoul.notification;

import dev.jazzybyte.onseoul.notification.adapter.in.web.CreateSubscriptionRequest;
import dev.jazzybyte.onseoul.notification.adapter.in.web.FilterDto;
import dev.jazzybyte.onseoul.notification.domain.NotificationChannel;
import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import jakarta.validation.ValidatorFactory;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * CreateSubscriptionRequest 의 Bean Validation(@AssertTrue) 레이어를 직접 검증한다.
 *
 * <p>컨트롤러 통합 테스트(MockMvc)는 400 응답만 확인하므로, 어떤 @AssertTrue 제약이
 * 위반되었는지까지 메시지 단위로 고정해 회귀를 막는다. 빈 구독 가드의 "DTO 레이어"
 * 절반을 담당한다 (나머지 절반은 NotificationSubscriptionService 가드 테스트).
 */
class CreateSubscriptionRequestValidationTest {

    private static ValidatorFactory factory;
    private static Validator validator;

    @BeforeAll
    static void setUp() {
        factory = Validation.buildDefaultValidatorFactory();
        validator = factory.getValidator();
    }

    @AfterAll
    static void tearDown() {
        factory.close();
    }

    private FilterDto keywords(String... kws) {
        return new FilterDto(Set.of(), Set.of(), Set.of(), Set.of(kws));
    }

    @Test
    @DisplayName("조건 1개(statuses) + 채널 → 위반 없음")
    void valid_passesAllConstraints() {
        var req = new CreateSubscriptionRequest(
                new FilterDto(Set.of("RECEIVING"), Set.of(), Set.of(), Set.of()),
                Set.of(NotificationChannel.EMAIL));

        Set<ConstraintViolation<CreateSubscriptionRequest>> violations = validator.validate(req);

        assertThat(violations).isEmpty();
    }

    @Test
    @DisplayName("빈 구독 — filter=null → isAtLeastOneConditionPresent 위반")
    void nullFilter_violatesAtLeastOneCondition() {
        var req = new CreateSubscriptionRequest(null, Set.of(NotificationChannel.EMAIL));

        var violations = validator.validate(req);

        assertThat(violations).anyMatch(v -> v.getPropertyPath().toString()
                .equals("atLeastOneConditionPresent"));
    }

    @Test
    @DisplayName("빈 구독 — 4개 조건 전부 빈 Set → isAtLeastOneConditionPresent 위반")
    void allFiltersEmpty_violatesAtLeastOneCondition() {
        var req = new CreateSubscriptionRequest(
                new FilterDto(Set.of(), Set.of(), Set.of(), Set.of()),
                Set.of(NotificationChannel.EMAIL));

        var violations = validator.validate(req);

        assertThat(violations).anyMatch(v -> v.getPropertyPath().toString()
                .equals("atLeastOneConditionPresent"));
    }

    @Test
    @DisplayName("키워드 정확히 3개(MAX_KEYWORDS 경계) → 위반 없음")
    void exactlyThreeKeywords_passes() {
        var req = new CreateSubscriptionRequest(
                keywords("a", "b", "c"), Set.of(NotificationChannel.EMAIL));

        var violations = validator.validate(req);

        assertThat(violations).isEmpty();
    }

    @Test
    @DisplayName("키워드 4개(초과) → isKeywordCountWithinLimit 위반")
    void fourKeywords_violatesLimit() {
        var req = new CreateSubscriptionRequest(
                keywords("a", "b", "c", "d"), Set.of(NotificationChannel.EMAIL));

        var violations = validator.validate(req);

        assertThat(violations).anyMatch(v -> v.getPropertyPath().toString()
                .equals("keywordCountWithinLimit"));
    }

    @Test
    @DisplayName("공백 키워드 포함 → isEachKeywordNonBlank 위반")
    void blankKeyword_violatesNonBlank() {
        var req = new CreateSubscriptionRequest(
                new FilterDto(Set.of(), Set.of(), Set.of(), Set.of("수영", "   ")),
                Set.of(NotificationChannel.EMAIL));

        var violations = validator.validate(req);

        assertThat(violations).anyMatch(v -> v.getPropertyPath().toString()
                .equals("eachKeywordNonBlank"));
    }

    @Test
    @DisplayName("채널 빈 Set → @NotEmpty(channels) 위반")
    void emptyChannels_violatesNotEmpty() {
        var req = new CreateSubscriptionRequest(
                new FilterDto(Set.of("RECEIVING"), Set.of(), Set.of(), Set.of()),
                Set.of());

        var violations = validator.validate(req);

        assertThat(violations).anyMatch(v -> v.getPropertyPath().toString().equals("channels"));
    }
}
