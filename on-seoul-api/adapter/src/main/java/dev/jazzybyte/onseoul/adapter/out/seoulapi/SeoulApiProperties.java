package dev.jazzybyte.onseoul.adapter.out.seoulapi;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import lombok.Getter;
import lombok.RequiredArgsConstructor;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Duration;

@Getter
@Setter
@ConfigurationProperties(prefix = "seoul.api")
@RequiredArgsConstructor
public class SeoulApiProperties {

    @NotBlank(message = "seoul.api.key 설정 값은 필수입니다.")
    private final String key;
    private String baseUrl = "http://openapi.seoul.go.kr:8088";
    @Min(value = 10, message = "pageSize는 최소 10 이상이어야 합니다.")
    @Max(value = 1000, message = "pageSize는 최대 1000 이하여야 합니다.")
    private int pageSize = 200;
    private int maxRetries = 3;
    private int maxBackoffSeconds = 10;
    private int connectTimeoutMs = 10_000;
    private int responseTimeoutSeconds = 30;

    public Duration getBlockTimeout() {
        return Duration.ofSeconds((responseTimeoutSeconds + 10L) * (maxRetries + 1));
    }
}
