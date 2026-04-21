package dev.jazzybyte.onseoul.collector.config;

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
    // non-final: @Setter로 생성되는 setter를 통해 Spring Boot yml 바인딩 및 테스트 오버라이드 가능
    private String baseUrl = "http://openapi.seoul.go.kr:8088";
    @Min(value = 10, message = "pageSize는 최소 10 이상이어야 합니다.")
    @Max(value = 1000, message = "pageSize는 최대 1000 이하여야 합니다.")
    private int pageSize = 200;
    private int maxRetries = 3;
    /** 재시도 간 최대 백오프 대기 시간 (초). 하루 1회 배치이므로 넉넉하게 설정 */
    private int maxBackoffSeconds = 10;
    /** TCP 연결 수립 타임아웃 (밀리초) */
    private int connectTimeoutMs = 10_000; // 기본값 10초
    /** HTTP 응답 수신 타임아웃 (초) */
    private int responseTimeoutSeconds = 30; // 기본값 30초

    

    /**
     * API 호출 시 최대 대기 시간
     * 각 요청마다 최대 responseTimeoutSeconds 초 대기하며, 최대 maxRetries 회 재시도하므로,
     * 총 대기 시간은 (responseTimeoutSeconds + 10초 여유) * (maxRetries + 1) 초로 설정한다.
     * @return
     */
    public Duration getBlockTimeout() {
        return Duration.ofSeconds((responseTimeoutSeconds + 10L) * (maxRetries + 1));
    }
}
