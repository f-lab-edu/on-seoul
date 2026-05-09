package dev.jazzybyte.onseoul.collector.config;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import jakarta.validation.ValidatorFactory;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;

import java.time.Duration;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("SeoulApiProperties 설정 검증")
class SeoulApiPropertiesTest {

    private static Validator validator;

    @BeforeAll
    static void setupValidator() {
        try (ValidatorFactory factory = Validation.buildDefaultValidatorFactory()) {
            validator = factory.getValidator();
        }
    }

    // ── 기본값 ────────────────────────────────────────────────────

    @Test
    @DisplayName("기본값이 올바르게 설정된다")
    void 기본값_확인() {
        SeoulApiProperties props = new SeoulApiProperties("test-key");

        assertThat(props.getBaseUrl()).isEqualTo("http://openapi.seoul.go.kr:8088");
        assertThat(props.getPageSize()).isEqualTo(200);
        assertThat(props.getMaxRetries()).isEqualTo(3);
        assertThat(props.getMaxBackoffSeconds()).isEqualTo(10);
        assertThat(props.getConnectTimeoutMs()).isEqualTo(10_000);
        assertThat(props.getResponseTimeoutSeconds()).isEqualTo(30);
    }

    // ── API 키 검증 ───────────────────────────────────────────────

    @Nested
    @DisplayName("API 키(key) 검증")
    class ApiKeyValidation {

        @Test
        @DisplayName("유효한 키는 위반이 없다")
        void 유효한_키는_통과() {
            SeoulApiProperties props = new SeoulApiProperties("valid-api-key");

            Set<ConstraintViolation<SeoulApiProperties>> violations = validator.validate(props);

            assertThat(violations).isEmpty();
        }

        @Test
        @DisplayName("키가 null이면 @NotBlank 위반")
        void null_키는_실패() {
            SeoulApiProperties props = new SeoulApiProperties(null);

            Set<ConstraintViolation<SeoulApiProperties>> violations = validator.validate(props);

            assertThat(violations).hasSize(1);
            assertThat(violations.iterator().next().getPropertyPath().toString()).isEqualTo("key");
            assertThat(violations.iterator().next().getMessage()).contains("seoul.api.key");
        }

        @Test
        @DisplayName("키가 빈 문자열이면 @NotBlank 위반")
        void 빈_문자열_키는_실패() {
            SeoulApiProperties props = new SeoulApiProperties("");

            Set<ConstraintViolation<SeoulApiProperties>> violations = validator.validate(props);

            assertThat(violations).hasSize(1);
            assertThat(violations.iterator().next().getPropertyPath().toString()).isEqualTo("key");
        }

        @Test
        @DisplayName("키가 공백 문자열이면 @NotBlank 위반")
        void 공백_문자열_키는_실패() {
            SeoulApiProperties props = new SeoulApiProperties("   ");

            Set<ConstraintViolation<SeoulApiProperties>> violations = validator.validate(props);

            assertThat(violations).hasSize(1);
            assertThat(violations.iterator().next().getPropertyPath().toString()).isEqualTo("key");
        }
    }

    // ── 페이지 크기 검증 ──────────────────────────────────────────

    @Nested
    @DisplayName("pageSize 범위 검증 (@Min 10, @Max 1000)")
    class PageSizeValidation {

        @Test
        @DisplayName("경계값 10은 통과한다")
        void pageSize_최솟값_경계() {
            SeoulApiProperties props = new SeoulApiProperties("key");
            props.setPageSize(10);

            assertThat(validator.validate(props)).isEmpty();
        }

        @Test
        @DisplayName("경계값 1000은 통과한다")
        void pageSize_최댓값_경계() {
            SeoulApiProperties props = new SeoulApiProperties("key");
            props.setPageSize(1000);

            assertThat(validator.validate(props)).isEmpty();
        }

        @Test
        @DisplayName("pageSize가 9이면 @Min 위반")
        void pageSize_최솟값_미만은_실패() {
            SeoulApiProperties props = new SeoulApiProperties("key");
            props.setPageSize(9);

            Set<ConstraintViolation<SeoulApiProperties>> violations = validator.validate(props);

            assertThat(violations).hasSize(1);
            assertThat(violations.iterator().next().getPropertyPath().toString()).isEqualTo("pageSize");
            assertThat(violations.iterator().next().getMessage()).contains("10");
        }

        @Test
        @DisplayName("pageSize가 1001이면 @Max 위반")
        void pageSize_최댓값_초과는_실패() {
            SeoulApiProperties props = new SeoulApiProperties("key");
            props.setPageSize(1001);

            Set<ConstraintViolation<SeoulApiProperties>> violations = validator.validate(props);

            assertThat(violations).hasSize(1);
            assertThat(violations.iterator().next().getPropertyPath().toString()).isEqualTo("pageSize");
            assertThat(violations.iterator().next().getMessage()).contains("1000");
        }
    }

    // ── getBlockTimeout 계산 ──────────────────────────────────────

    @Nested
    @DisplayName("getBlockTimeout() 계산")
    class BlockTimeoutCalculation {

        @Test
        @DisplayName("기본값: (30 + 10) × (3 + 1) = 160초")
        void 기본_타임아웃_계산() {
            SeoulApiProperties props = new SeoulApiProperties("key");
            // responseTimeoutSeconds=30, maxRetries=3 → (30+10) × (3+1) = 160s

            assertThat(props.getBlockTimeout()).isEqualTo(Duration.ofSeconds(160));
        }

        @Test
        @DisplayName("responseTimeoutSeconds와 maxRetries 변경 시 재계산된다")
        void 커스텀_타임아웃_계산() {
            SeoulApiProperties props = new SeoulApiProperties("key");
            props.setResponseTimeoutSeconds(20);
            props.setMaxRetries(2);
            // (20 + 10) × (2 + 1) = 90s

            assertThat(props.getBlockTimeout()).isEqualTo(Duration.ofSeconds(90));
        }

        @Test
        @DisplayName("재시도 0회 설정 시 단일 요청 기준으로 계산된다")
        void 재시도_없을때_타임아웃() {
            SeoulApiProperties props = new SeoulApiProperties("key");
            props.setResponseTimeoutSeconds(30);
            props.setMaxRetries(0);
            // (30 + 10) × (0 + 1) = 40s

            assertThat(props.getBlockTimeout()).isEqualTo(Duration.ofSeconds(40));
        }
    }
}
