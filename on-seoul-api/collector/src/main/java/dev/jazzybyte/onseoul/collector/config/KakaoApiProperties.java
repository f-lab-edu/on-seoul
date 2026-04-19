package dev.jazzybyte.onseoul.collector.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;

@Getter
@Setter
@ConfigurationProperties(prefix = "kakao.api")
public class KakaoApiProperties {

    /** 카카오 REST API 키. 미설정(빈 문자열)이면 Geocoding sweep을 스킵한다. */
    private String key = "";
    private String baseUrl = "https://dapi.kakao.com";
}
