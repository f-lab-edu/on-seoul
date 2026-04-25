package dev.jazzybyte.onseoul.adapter.out.kakao;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;

@Getter
@Setter
@ConfigurationProperties(prefix = "kakao.api")
public class KakaoApiProperties {

    private String key = "";
    private String baseUrl = "https://dapi.kakao.com";
}
