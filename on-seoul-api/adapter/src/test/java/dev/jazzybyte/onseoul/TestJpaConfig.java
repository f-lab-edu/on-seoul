package dev.jazzybyte.onseoul;

import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * @DataJpaTest가 @SpringBootConfiguration을 찾기 위한 최소 설정 클래스.
 * adapter 모듈에는 @SpringBootApplication이 없으므로 테스트 전용으로 제공한다.
 */
@SpringBootApplication
class TestJpaConfig {
}
