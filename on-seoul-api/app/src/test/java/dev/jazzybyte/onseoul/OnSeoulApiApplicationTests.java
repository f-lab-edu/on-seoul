package dev.jazzybyte.onseoul;

import dev.jazzybyte.onseoul.collector.service.CollectionService;
import dev.jazzybyte.onseoul.collector.service.GeocodingService;
import dev.jazzybyte.onseoul.collector.service.UpsertService;
import lombok.extern.slf4j.Slf4j;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

/**
 * 애플리케이션 컨텍스트 로딩 검증.
 *
 * <p>DB/Redis 실제 연결은 로컬 환경에서 .env를 통한 bootRun으로 검증한다.
 * 자동 설정을 제외해 외부 인프라 없이도 컨텍스트 구성 오류를 감지할 수 있다.</p>
 */
@Slf4j
@SpringBootTest
@TestPropertySource(properties = {
        "spring.autoconfigure.exclude=" +
        "org.springframework.boot.autoconfigure.jdbc.DataSourceAutoConfiguration," +
        "org.springframework.boot.autoconfigure.orm.jpa.HibernateJpaAutoConfiguration," +
        "org.springframework.boot.autoconfigure.data.redis.RedisAutoConfiguration," +
        "org.springframework.boot.autoconfigure.data.redis.RedisReactiveAutoConfiguration"
})
class OnSeoulApiApplicationTests {

    // JPA/Redis가 제외된 테스트 컨텍스트에서 JPA 의존 빈을 대체한다
    @MockitoBean
    CollectionService collectionService;

    @MockitoBean
    UpsertService upsertService;

    @MockitoBean
    GeocodingService geocodingService;

    @Test
    void contextLoads(ApplicationContext applicationContext) {
        String[] beanNames = applicationContext.getBeanDefinitionNames();
        long onSeoulBeans = 0;
        for (String beanName : beanNames) {
            Object bean = applicationContext.getBean(beanName);
            String packageName = bean.getClass().getPackage() != null
                    ? bean.getClass().getPackage().getName()
                    : "";
            if (packageName.startsWith("dev.jazzybyte.onseoul")) {
                log.info("Bean: {} ({})", beanName, packageName);
                onSeoulBeans++;
            }
        }
        log.info("Total dev.jazzybyte.onseoul beans: {}", onSeoulBeans);
    }

}
