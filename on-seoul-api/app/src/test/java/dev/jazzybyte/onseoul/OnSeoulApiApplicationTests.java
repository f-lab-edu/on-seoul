package dev.jazzybyte.onseoul;

import lombok.extern.slf4j.Slf4j;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.context.ApplicationContext;
import org.springframework.test.context.TestPropertySource;

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
